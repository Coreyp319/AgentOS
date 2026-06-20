#!/usr/bin/env bash
# preview_shot.sh — render a clean still of the saved CalmWallpaper scene from
# AgentOS_Camera, headless, no Remote Execution. The reliable launcher for the
# author -> preview -> look loop (launch_offscreen.sh uses setsid, which gets
# SIGTERM'd on tool-call cleanup in this env; this uses plain & + a watchdog,
# like author_scene.sh, and is meant to run via the Bash tool with
# run_in_background:true).
#
# USAGE
#   bash spikes/ue-probe/preview_shot.sh
# It opens the editor INTO /Game/AgentOS/CalmWallpaper, runs preview_shot.py
# (which points the viewport at the CineCamera, game-views, lets Lumen converge,
# then HighResShots), waits for the 'DONE preview_shot' marker, kills the editor,
# and prints the newest screenshot path so the caller can Read it.
#
# SAFETY: rendering an UNLIT preview is light (no ~22 GB Lumen cold-compile), so
# the VRAM gate is modest — but still refuses if a real gen is eating the card.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EDITOR="${HOME}/UnrealEngine/Engine/Binaries/Linux/UnrealEditor"   # full editor (renders), offscreen
PROJECT="${HOME}/UnrealProjects/AgentOSBlank/AgentOSBlank.uproject"
MAP="${MAP:-/Game/AgentOS/CalmWallpaper}"
PREVIEW_PY="${SCRIPT_DIR}/preview_shot.py"
LOG="${SCRIPT_DIR}/ue_preview.log"
SHOTDIR="${HOME}/UnrealProjects/AgentOSBlank/Saved/Screenshots"

MIN_FREE_MIB="${MIN_FREE_MIB:-9000}"   # preview is light; just avoid colliding a gen
TIMEOUT="${PREVIEW_TIMEOUT:-150}"
MARK='DONE preview_shot'

[[ -x "$EDITOR"     ]] || { echo "FAIL: editor not found: $EDITOR" >&2; exit 1; }
[[ -f "$PROJECT"    ]] || { echo "FAIL: project not found: $PROJECT" >&2; exit 1; }
[[ -f "$PREVIEW_PY" ]] || { echo "FAIL: preview script not found: $PREVIEW_PY" >&2; exit 1; }

FREE_MIB="$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d ' ')"
echo "VRAM free: ${FREE_MIB:-unknown} MiB (need >= ${MIN_FREE_MIB} MiB)"
if [[ "${PREVIEW_FORCE:-0}" != "1" ]]; then
  if [[ -z "$FREE_MIB" || "$FREE_MIB" -lt "$MIN_FREE_MIB" ]]; then
    echo "ABORT: not enough free VRAM (card looks busy with the user's gen). Re-run when clear."
    exit 3
  fi
fi

# record the newest screenshot BEFORE, so we can detect the new one after.
PREV_SHOT="$(ls -t "$SHOTDIR"/**/*.png "$SHOTDIR"/*.png 2>/dev/null | head -n1 || true)"

# NO inner quotes around the -ExecCmds path (UE re-quotes the value itself). NO
# trailing Quit: preview_shot.py drives its own converge/shoot/flush via a slate
# post-tick callback; a Quit would fire the instant the script registers it.
ARGS=(
  "$PROJECT" "$MAP"
  -RenderOffscreen -unattended -stdout -FullStdOutLogOutput
  -ResX=1920 -ResY=1080 -nosound -nosplash
  "-ExecCmds=py ${PREVIEW_PY}"
)

echo "rendering preview of $MAP ..."
echo "  log: $LOG"
"$EDITOR" "${ARGS[@]}" > "$LOG" 2>&1 &
UE_PID=$!
echo "editor pid=$UE_PID (watchdog ${TIMEOUT}s)"

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
echo "=== editor stopped (verdict=$verdict after ${waited}s) ==="

echo "--- preview markers ---"
grep -naE 'HighResShot .* issued|DONE preview_shot|viewport ->|WARN' "$LOG" | tail -n 8

NEW_SHOT="$(ls -t "$SHOTDIR"/**/*.png "$SHOTDIR"/*.png 2>/dev/null | head -n1 || true)"
if [[ -n "$NEW_SHOT" && "$NEW_SHOT" != "$PREV_SHOT" ]]; then
  echo "PASS: new screenshot -> $NEW_SHOT"
  ls -la --time-style=+%H:%M:%S "$NEW_SHOT"
  exit 0
else
  echo "FAIL: no new screenshot under $SHOTDIR (verdict=$verdict). Inspect $LOG."
  exit 4
fi
