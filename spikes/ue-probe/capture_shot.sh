#!/usr/bin/env bash
# capture_shot.sh — render a TRUE scene-capture still of the saved CalmWallpaper
# (Indigo Channel) from AgentOS_Camera using a SceneCaptureComponent2D ->
# TextureRenderTarget2D -> PNG, headless, no Remote Execution.
#
# WHY (vs preview_shot.sh AND game_shot.sh):
#   * preview_shot.sh drives the EDITOR perspective viewport (HighResShot). That
#     shows the height-fog inscatter BACKGROUND but does NOT faithfully resolve the
#     volumetric-fog froxel SHAFTS (god-ray beams) — the thing we verify.
#   * game_shot.sh launches the editor in -game + HighResShot and SIGSEGVs. The crash
#     (ue_game_shot.log:1985-2000) is the DEPRECATED EditorLevelLibrary.get_editor_
#     world() null-derefing the editor-subsystem collection under -game, BEFORE the
#     shot. Not a high-res-readback bug; a no-editor-world bug.
#
#   capture_shot.py does a real SceneCaptureComponent2D scene render (full deferred
#   path, volumetric fog included) INTO a render target, driven from the normal
#   EDITOR world (UnrealEditor-Cmd -RenderOffscreen, NOT -game) — true volumetric
#   render, no -game crash. It also stamps the world PPV's AEM_MANUAL bias onto the
#   capture's own post-process so brightness matches the runtime (a SceneCapture does
#   NOT inherit world PPV exposure and self-auto-exposes by default).
#
# It is a near-exact mirror of preview_shot.sh: same VRAM pre-flight gate
# (MIN_FREE_MIB + CAPTURE_FORCE override), same background-launch + watchdog +
# success-marker + newest-PNG detection, same -ExecCmds NO-inner-quotes rule. The
# differences: it uses UnrealEditor-Cmd (NOT -game), chains the fog CVARs ahead of
# the py command, looks for PNGs under Saved/Screenshots/Capture/, and capture_shot.py
# Quits itself on completion (the watchdog is only a backstop).
#
# USAGE
#   bash spikes/ue-probe/capture_shot.sh
# Optional env:
#   CAPTURE_FORCE=1     bypass the VRAM gate (you KNOW the card is clear)
#   MIN_FREE_MIB=9000   required free VRAM before launch
#   CAPTURE_TIMEOUT=180 watchdog seconds
#   MAP=/Game/AgentOS/CalmWallpaper
#   CVAR_EXTRA="r.Foo 1"  append more diagnostic CVARs without editing this file

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# UnrealEditor-Cmd (NOT -game): a real editor world that renders the full deferred
# path through a SceneCaptureComponent2D, with no -game editor-world crash.
EDITOR="${HOME}/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd"
PROJECT="${HOME}/UnrealProjects/AgentOSBlank/AgentOSBlank.uproject"
MAP="${MAP:-/Game/AgentOS/CalmWallpaper}"
CAPTURE_PY="${SCRIPT_DIR}/capture_shot.py"
LOG="${SCRIPT_DIR}/ue_capture_shot.log"
# capture_shot.py writes to a dedicated Capture/ subdir so newest-PNG detection
# can't pick up a stale HighResShot from LinuxEditor/.
SHOTDIR="${HOME}/UnrealProjects/AgentOSBlank/Saved/Screenshots/Capture"

MIN_FREE_MIB="${MIN_FREE_MIB:-9000}"   # a scene-capture render is light; just avoid colliding a gen
TIMEOUT="${CAPTURE_TIMEOUT:-180}"
MARK='DONE capture_shot'

[[ -x "$EDITOR"     ]] || { echo "FAIL: editor-cmd not found: $EDITOR" >&2; exit 1; }
[[ -f "$PROJECT"    ]] || { echo "FAIL: project not found: $PROJECT" >&2; exit 1; }
[[ -f "$CAPTURE_PY" ]] || { echo "FAIL: capture script not found: $CAPTURE_PY" >&2; exit 1; }

# ---- VRAM pre-flight (card is SHARED with the user's gens) -----------------
FREE_MIB="$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d ' ')"
echo "VRAM free: ${FREE_MIB:-unknown} MiB (need >= ${MIN_FREE_MIB} MiB)"
if [[ "${CAPTURE_FORCE:-0}" != "1" ]]; then
  if [[ -z "$FREE_MIB" || "$FREE_MIB" -lt "$MIN_FREE_MIB" ]]; then
    echo "ABORT: not enough free VRAM (card looks busy with the user's gen). Re-run when clear, or CAPTURE_FORCE=1."
    exit 3
  fi
fi

mkdir -p "$SHOTDIR" 2>/dev/null || true
# record the newest capture PNG BEFORE, so we can detect the new one after.
PREV_SHOT="$(ls -t "$SHOTDIR"/*.png 2>/dev/null | head -n1 || true)"

# VOLUMETRIC-FOG DIAGNOSTIC CVARS (same intent as game_shot.sh): force the fog path
# on so a scalability default can't silently disable the shaft, and raise the grid
# resolution so thin shafts aren't under-sampled into nothing. Comma-chained into the
# SAME -ExecCmds value (UE splits on commas), BEFORE the `py` command, so they apply
# before the capture renders. NO inner quotes anywhere (see README gotcha #1 /
# author_scene.sh: UE re-quotes the whole -ExecCmds value; our quotes would collide
# and FParse would read only `py ` with the path lost).
FOG_CVARS="r.VolumetricFog 1, r.VolumetricFog.GridPixelSize 4, r.VolumetricFog.GridSizeZ 256, r.VolumetricFog.InjectRaytracedLights 1, r.Shadow.CSM.MaxCascades 4"
[[ -n "${CVAR_EXTRA:-}" ]] && FOG_CVARS="${FOG_CVARS}, ${CVAR_EXTRA}"

# NO trailing Quit in -ExecCmds: capture_shot.py drives its own converge/capture/
# export/flush via a slate post-tick and issues `Quit` itself when DONE. A Quit here
# would fire the instant the script registers its callback.
ARGS=(
  "$PROJECT" "$MAP"
  -RenderOffscreen -unattended -stdout -FullStdOutLogOutput
  -ResX=1920 -ResY=1080 -nosound -nosplash
  "-ExecCmds=${FOG_CVARS}, py ${CAPTURE_PY}"
)

echo "rendering SceneCapture2D still of $MAP (true volumetric, editor world) ..."
echo "  log: $LOG"
"$EDITOR" "${ARGS[@]}" > "$LOG" 2>&1 &
UE_PID=$!
echo "editor(-cmd) pid=$UE_PID (watchdog ${TIMEOUT}s)"

waited=0; verdict="timeout"
while [ "$waited" -lt "$TIMEOUT" ]; do
  if ! kill -0 "$UE_PID" 2>/dev/null; then verdict="exited"; break; fi
  if grep -qa "$MARK" "$LOG" 2>/dev/null; then verdict="marker"; break; fi
  sleep 3; waited=$((waited + 3))
done
# capture_shot.py exports BEFORE the DONE marker + then self-Quits; give the PNG a
# moment to flush, then force-stop only if it lingers.
[ "$verdict" = "marker" ] && sleep 3
if kill -0 "$UE_PID" 2>/dev/null; then
  kill -TERM "$UE_PID" 2>/dev/null; sleep 2; kill -9 "$UE_PID" 2>/dev/null
fi
wait "$UE_PID" 2>/dev/null
echo "=== editor stopped (verdict=$verdict after ${waited}s) ==="

echo "--- capture markers ---"
grep -naE 'capture_scene\(\) x|export_render_target ->|DONE capture_shot|world PPV exposure|capture exposure pinned|SceneCapture2D spawned|WARN|not found' "$LOG" | tail -n 12
echo "--- map-load confirm (prove it rendered OUR scene) ---"
grep -naE 'LoadMap Load map complete /Game/AgentOS/CalmWallpaper|Bringing World /Game/AgentOS/CalmWallpaper' "$LOG" | tail -n 2
echo "--- python errors (should be empty) ---"
grep -naiE 'LogPython:.*(error|traceback)|\[capture\].*(FAILED|could not|NOT READY)|AttributeError|TypeError|RuntimeError' "$LOG" | tail -n 8

NEW_SHOT="$(ls -t "$SHOTDIR"/*.png 2>/dev/null | head -n1 || true)"
if [[ -n "$NEW_SHOT" && "$NEW_SHOT" != "$PREV_SHOT" ]]; then
  echo "PASS: new scene-capture PNG -> $NEW_SHOT"
  ls -la --time-style=+%H:%M:%S "$NEW_SHOT"
  exit 0
else
  echo "FAIL: no new PNG under $SHOTDIR (verdict=$verdict). Inspect $LOG."
  exit 4
fi
