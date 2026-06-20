#!/usr/bin/env bash
# author_scene.sh — headlessly author the calm Lumen wallpaper scene and SAVE it.
#
# WHY THIS EXISTS
#   The first author attempt failed because the command was built with a nested
#   `eval`, which collapsed the escaped inner quotes inside -ExecCmds=... — UE
#   received `-ExecCmds=py` with NO filename (the log shows `Cmd: py ` then
#   nothing) and the script path fell off as a stray positional arg. scene_setup.py
#   never ran, so no /Game/AgentOS/CalmWallpaper was saved.
#
#   This script builds the argv as a real bash ARRAY (no eval), so the
#   `-ExecCmds=py "<abs path>", Quit` token survives intact as ONE argument.
#   It runs headless (-unattended -RenderOffscreen), runs scene_setup.py, which
#   saves the level, then Quit exits the editor. Pure authoring; no idle editor.
#
# USAGE
#   Launch via the Bash tool with run_in_background:true (NOT setsid — a setsid
#   editor gets SIGTERM'd when the launching tool call returns). e.g.
#       bash spikes/ue-probe/author_scene.sh
#   It blocks until the editor exits, then prints PASS/FAIL and the saved path.
#
# SAFETY (VRAM management IS the product)
#   Authoring a Lumen scene has spiked to ~22 GB transiently (shader perms +
#   Lumen). The card is shared with Corey's lucid/ComfyUI gens, which can balloon
#   to ~17 GB with no warning. So we PRE-FLIGHT: require >= MIN_FREE_MIB free
#   before launching, else abort. Override with AUTHOR_FORCE=1 if you KNOW the
#   card is clear.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CMD="${HOME}/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd"
PROJECT="${HOME}/UnrealProjects/AgentOSBlank/AgentOSBlank.uproject"
# Which authoring script to run + which success marker to wait for. Defaults
# build the primitive scaffold (scene_setup.py); override to author the
# gradient-wave tableau, e.g.:
#   SCENE_SCRIPT=gradient_wave_setup.py MARK='Abyssal gradient-wave scene built' \
#     bash spikes/ue-probe/author_scene.sh
SCENE="${SCRIPT_DIR}/${SCENE_SCRIPT:-scene_setup.py}"
LOG="${SCRIPT_DIR}/ue_author.log"
LEVEL_UMAP="${HOME}/UnrealProjects/AgentOSBlank/Content/AgentOS/CalmWallpaper.umap"

MIN_FREE_MIB="${MIN_FREE_MIB:-18000}"   # headroom for the ~22 GB authoring spike

# ---- preconditions --------------------------------------------------------
[[ -x "$CMD"     ]] || { echo "FAIL: editor-cmd not found: $CMD" >&2; exit 1; }
[[ -f "$PROJECT" ]] || { echo "FAIL: project not found: $PROJECT" >&2; exit 1; }
[[ -f "$SCENE"   ]] || { echo "FAIL: scene script not found: $SCENE" >&2; exit 1; }

# ---- VRAM pre-flight ------------------------------------------------------
FREE_MIB="$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d ' ')"
echo "VRAM free: ${FREE_MIB:-unknown} MiB (need >= ${MIN_FREE_MIB} MiB for the authoring spike)"
if [[ "${AUTHOR_FORCE:-0}" != "1" ]]; then
  if [[ -z "$FREE_MIB" || "$FREE_MIB" -lt "$MIN_FREE_MIB" ]]; then
    echo "ABORT: not enough free VRAM (or unreadable). The card looks busy with the user's work."
    echo "       Authoring a Lumen scene can transiently need ~22 GB and would risk OOM-ing"
    echo "       Corey's lucid/ComfyUI gens. Re-run when the card is clear, or AUTHOR_FORCE=1 to override."
    exit 3
  fi
fi

# ---- launch (argv array — NO eval, NO inner quotes) -----------------------
# CRITICAL quoting lesson (cost two failed runs + an 8h zombie):
#   UE reconstructs its command line and wraps a `-Key=value with spaces` token
#   as `-Key="value with spaces"` itself. If WE add inner quotes around the path,
#   UE's wrapping + our quotes collide -> FParse reads the value as just `py `
#   (path lost), the log shows `Cmd: py ` empty, and `Quit` never fires either,
#   so the editor idles forever. FIX: pass NO quotes. The path has no spaces, so
#   `-ExecCmds=py /abs/path.py, Quit` -> UE wraps it -> `-ExecCmds="py /abs/path.py, Quit"`
#   -> FParse gets the full value -> comma-split -> [ py /abs/path.py ] then [ Quit ].
ARGS=(
  "$PROJECT"
  -RenderOffscreen
  -unattended
  -stdout
  -FullStdOutLogOutput
  "-ExecCmds=py ${SCENE}, Quit"
)

echo "authoring CalmWallpaper headless..."
echo "  cmd  : $CMD"
echo "  proj : $PROJECT"
echo "  exec : ${ARGS[-1]}"
echo "  log  : $LOG"

# Run in background + watchdog so a future quoting regression can NEVER strand an
# idle editor again. Authoring is <2 min; if we don't see the success marker (or
# a natural exit) within AUTHOR_TIMEOUT, we kill it and report FAIL.
TIMEOUT="${AUTHOR_TIMEOUT:-360}"
MARK="${MARK:-calm Lumen wallpaper scene built}"

"$CMD" "${ARGS[@]}" > "$LOG" 2>&1 &
UE_PID=$!
echo "editor pid=$UE_PID (watchdog ${TIMEOUT}s)"

waited=0; verdict="timeout"
while [ "$waited" -lt "$TIMEOUT" ]; do
  if ! kill -0 "$UE_PID" 2>/dev/null; then verdict="exited"; break; fi
  if grep -q "$MARK" "$LOG" 2>/dev/null; then verdict="marker"; break; fi
  sleep 3; waited=$((waited + 3))
done

# Ensure the editor is gone — Quit should have fired on its own; force-stop if the
# marker landed but it lingered, or if we timed out.
if kill -0 "$UE_PID" 2>/dev/null; then
  echo "stopping editor (verdict=$verdict, ${waited}s)"
  kill -TERM "$UE_PID" 2>/dev/null; sleep 2; kill -9 "$UE_PID" 2>/dev/null
fi
wait "$UE_PID" 2>/dev/null
echo "=== editor stopped (verdict=$verdict after ${waited}s) ==="

# ---- verdict --------------------------------------------------------------
echo "--- scene_setup markers ---"
grep -nE 'AgentOS scene_setup|calm Lumen wallpaper scene built|save_current_level|new_level' "$LOG" | tail -n 12
echo "--- python errors (should be empty) ---"
grep -niE 'LogPython:.*(error|traceback)|AttributeError|TypeError|RuntimeError|ModuleNotFound' "$LOG" | tail -n 8

if [[ -f "$LEVEL_UMAP" ]] && grep -q "$MARK" "$LOG"; then
  echo "PASS: scene authored + saved -> $LEVEL_UMAP"
  ls -la --time-style=+%H:%M:%S "$LEVEL_UMAP"
  exit 0
else
  echo "FAIL: no saved umap and/or no 'scene built' marker. Inspect $LOG (search 'Cmd: py')."
  exit 4
fi
