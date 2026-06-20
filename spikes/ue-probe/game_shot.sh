#!/usr/bin/env bash
# game_shot.sh — render a TRUE -game still of the saved CalmWallpaper scene from
# AgentOS_Camera (auto_activate PLAYER0), headless, no Remote Execution.
#
# WHY (vs preview_shot.sh): preview_shot.sh drives the EDITOR viewport, which
# applies its own EV100/auto exposure and can MISREPRESENT the shipped exposure.
# This launches the editor binary in -game so the REAL runtime renders through the
# PostProcessVolume's AEM_MANUAL bias and Player0 = AgentOS_Camera. Use THIS to
# judge exposure; use preview_shot.sh for fast composition checks only.
#
# It is a near-exact mirror of preview_shot.sh: same VRAM pre-flight gate
# (MIN_FREE_MIB + *_FORCE override), same background-launch + watchdog +
# success-marker + newest-PNG detection, same -ExecCmds NO-inner-quotes rule. The
# ONLY differences are the added `-game` switch and that -game writes PNGs to
# Saved/Screenshots/LinuxGame/ (not LinuxEditor/).
#
# USAGE
#   bash spikes/ue-probe/game_shot.sh
# Optional env (same spirit as preview_shot.sh):
#   GAME_FORCE=1        bypass the VRAM gate (you KNOW the card is clear)
#   MIN_FREE_MIB=9000   required free VRAM before launch
#   GAME_TIMEOUT=180    watchdog seconds
#   MAP=/Game/AgentOS/CalmWallpaper

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EDITOR="${HOME}/UnrealEngine/Engine/Binaries/Linux/UnrealEditor"   # editor binary, run as -game (renders through real PPV exposure)
PROJECT="${HOME}/UnrealProjects/AgentOSBlank/AgentOSBlank.uproject"
MAP="${MAP:-/Game/AgentOS/CalmWallpaper}"
GAME_PY="${SCRIPT_DIR}/game_shot.py"
LOG="${SCRIPT_DIR}/ue_game_shot.log"
SHOTDIR="${HOME}/UnrealProjects/AgentOSBlank/Saved/Screenshots"

MIN_FREE_MIB="${MIN_FREE_MIB:-9000}"   # a -game render is light; just avoid colliding a gen
TIMEOUT="${GAME_TIMEOUT:-180}"
MARK='DONE game_shot'

[[ -x "$EDITOR"  ]] || { echo "FAIL: editor not found: $EDITOR" >&2; exit 1; }
[[ -f "$PROJECT" ]] || { echo "FAIL: project not found: $PROJECT" >&2; exit 1; }
[[ -f "$GAME_PY" ]] || { echo "FAIL: game-shot script not found: $GAME_PY" >&2; exit 1; }

FREE_MIB="$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d ' ')"
echo "VRAM free: ${FREE_MIB:-unknown} MiB (need >= ${MIN_FREE_MIB} MiB)"
if [[ "${GAME_FORCE:-0}" != "1" ]]; then
  if [[ -z "$FREE_MIB" || "$FREE_MIB" -lt "$MIN_FREE_MIB" ]]; then
    echo "ABORT: not enough free VRAM (card looks busy with the user's gen). Re-run when clear, or GAME_FORCE=1."
    exit 3
  fi
fi

# record the newest screenshot BEFORE (across BOTH LinuxGame and LinuxEditor), so
# we can detect the new one after. -game writes to LinuxGame/.
PREV_SHOT="$(ls -t "$SHOTDIR"/**/*.png "$SHOTDIR"/*.png 2>/dev/null | head -n1 || true)"

# NO inner quotes around the -ExecCmds path (UE re-quotes the value itself). NO
# trailing Quit: game_shot.py drives its own converge/shoot/flush via a slate
# post-tick callback; a Quit would fire the instant the script registers it.
#
# -game makes the editor binary run the real game world (Player0 = AgentOS_Camera,
# shipped PostProcessVolume exposure) — the whole reason this script exists.
#
# VOLUMETRIC-FOG DIAGNOSTIC CVARS (Priority-1 audit). These FORCE the fog path on
# at runtime so a scalability/-game default can't silently disable the shaft, and
# they raise the grid resolution so thin shafts aren't under-sampled into nothing.
# They are comma-chained into the SAME -ExecCmds value (UE splits on commas), BEFORE
# the `py` command, so they apply before the shot renders. NO inner quotes anywhere.
#   r.VolumetricFog 1                 — force the volume on (defaults can vary by run)
#   r.VolumetricFog.GridPixelSize 4   — finer XY grid (default 8) → crisper shaft edges
#   r.VolumetricFog.GridSizeZ 256     — more depth slices (default 128) → smoother shaft
#   r.VolumetricFog.InjectRaytracedLights 1 — harmless if RT off; fixes RT-shadow case
#   r.Shadow.CSM.MaxCascades 4        — ensure CSM exists (CSM is what carves the rays)
# Tune via env CVAR_EXTRA="r.Foo 1" to append more without editing this file.
FOG_CVARS="r.VolumetricFog 1, r.VolumetricFog.GridPixelSize 4, r.VolumetricFog.GridSizeZ 256, r.VolumetricFog.InjectRaytracedLights 1, r.Shadow.CSM.MaxCascades 4"
[[ -n "${CVAR_EXTRA:-}" ]] && FOG_CVARS="${FOG_CVARS}, ${CVAR_EXTRA}"
ARGS=(
  "$PROJECT" "$MAP"
  -game
  -RenderOffscreen -unattended -stdout -FullStdOutLogOutput
  -ResX=1920 -ResY=1080 -nosound -nosplash
  "-ExecCmds=${FOG_CVARS}, py ${GAME_PY}"
)

echo "rendering -game still of $MAP (TRUE runtime exposure) ..."
echo "  log: $LOG"
"$EDITOR" "${ARGS[@]}" > "$LOG" 2>&1 &
UE_PID=$!
echo "editor(-game) pid=$UE_PID (watchdog ${TIMEOUT}s)"

waited=0; verdict="timeout"
while [ "$waited" -lt "$TIMEOUT" ]; do
  if ! kill -0 "$UE_PID" 2>/dev/null; then verdict="exited"; break; fi
  if grep -qa "$MARK" "$LOG" 2>/dev/null; then verdict="marker"; break; fi
  sleep 3; waited=$((waited + 3))
done
# give the PNG a moment to flush after the marker, then stop the editor.
[ "$verdict" = "marker" ] && sleep 3
if kill -0 "$UE_PID" 2>/dev/null; then
  kill -TERM "$UE_PID" 2>/dev/null; sleep 2; kill -9 "$UE_PID" 2>/dev/null
fi
wait "$UE_PID" 2>/dev/null
echo "=== editor(-game) stopped (verdict=$verdict after ${waited}s) ==="

echo "--- game_shot markers ---"
grep -naE 'HighResShot .* issued|DONE game_shot|game world|WARN' "$LOG" | tail -n 8
echo "--- map-load confirm (prove it rendered OUR scene, not an empty template) ---"
grep -naE 'LoadMap Load map complete /Game/AgentOS/CalmWallpaper|Bringing World /Game/AgentOS/CalmWallpaper' "$LOG" | tail -n 2

NEW_SHOT="$(ls -t "$SHOTDIR"/**/*.png "$SHOTDIR"/*.png 2>/dev/null | head -n1 || true)"
if [[ -n "$NEW_SHOT" && "$NEW_SHOT" != "$PREV_SHOT" ]]; then
  echo "PASS: new -game screenshot -> $NEW_SHOT"
  case "$NEW_SHOT" in
    *LinuxGame*) echo "  (LinuxGame/ — confirms TRUE -game runtime exposure path)";;
    *) echo "  WARN: PNG is NOT under LinuxGame/ — verify -game actually took effect, this may be an editor-path shot.";;
  esac
  ls -la --time-style=+%H:%M:%S "$NEW_SHOT"
  exit 0
else
  echo "FAIL: no new screenshot under $SHOTDIR (verdict=$verdict). Inspect $LOG."
  exit 4
fi
