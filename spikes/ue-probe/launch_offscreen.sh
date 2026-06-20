#!/usr/bin/env bash
# launch_offscreen.sh — launch UnrealEditor (UE 5.8, Lumen) offscreen + detached,
# applying a cvar throttle rung at startup, for VRAM/feasibility probing.
#
# This script ONLY launches the editor. It does NOT sample the GPU — pair it with
# sample_vram.sh in a second terminal.
#
# Usage:
#   ./launch_offscreen.sh --exec "<cvar string>" [--map <map path or .umap>]
#
#   --exec   Required. The comma-separated console commands for -ExecCmds
#            (a rung from cvar_ladder.md, or e.g. 'py "/abs/path/scene_setup.py"').
#   --map    Optional. A map to open: either a /Game/... package path
#            (e.g. /Game/AgentOS/CalmWallpaper) or a .umap file path. If omitted,
#            the project's default map loads.
#
# Examples:
#   # Build the scene once (runs scene_setup.py, saves the level, then idles):
#   ./launch_offscreen.sh --exec 'py "/home/corey/Documents/AgentOS/spikes/ue-probe/scene_setup.py"'
#
#   # Measure the FLOOR rung against the saved calm scene:
#   ./launch_offscreen.sh --map /Game/AgentOS/CalmWallpaper \
#     --exec "r.ScreenPercentage 50, sg.GlobalIlluminationQuality 0, sg.ShadowQuality 0, sg.ReflectionQuality 0, sg.PostProcessQuality 0, r.Streaming.PoolSize 512, r.Streaming.LimitPoolSizeToVRAM 1, t.MaxFPS 5"
#
# The launch is DETACHED via setsid, stdout/stderr go to a timestamped log under
# this directory, and the script prints the log path + how to find/kill the PID.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EDITOR="${HOME}/UnrealEngine/Engine/Binaries/Linux/UnrealEditor"
PROJECT="${HOME}/UnrealProjects/AgentOSBlank/AgentOSBlank.uproject"

EXEC_CMDS=""
MAP=""

# ---- args -----------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --exec)
      EXEC_CMDS="${2:-}"; shift 2 ;;
    --map)
      MAP="${2:-}"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)
      echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$EXEC_CMDS" ]]; then
  echo "error: --exec \"<cvar string>\" is required" >&2
  exit 2
fi
if [[ ! -x "$EDITOR" ]]; then
  echo "error: editor not found/executable: $EDITOR" >&2
  exit 1
fi
if [[ ! -f "$PROJECT" ]]; then
  echo "error: project not found: $PROJECT" >&2
  exit 1
fi

# ---- launch ---------------------------------------------------------------
TS="$(date +%Y%m%d_%H%M%S)"
LOG="${SCRIPT_DIR}/ue_run_${TS}.log"
PIDFILE="${SCRIPT_DIR}/ue_run_${TS}.pid"

# UnrealEditor positional args: <project> [<map>] then switches.
# A /Game/... path is passed as-is; UE resolves the package.
ARGS=("$PROJECT")
if [[ -n "$MAP" ]]; then
  ARGS+=("$MAP")
fi
ARGS+=(
  -RenderOffscreen
  -unattended
  -stdout
  -FullStdOutLogOutput
  "-ExecCmds=${EXEC_CMDS}"
)

echo "launching UnrealEditor (detached, offscreen)"
echo "  editor : $EDITOR"
echo "  project: $PROJECT"
[[ -n "$MAP" ]] && echo "  map    : $MAP"
echo "  exec   : $EXEC_CMDS"
echo "  log    : $LOG"

# setsid -> own session, fully detached from this shell; survives terminal close.
setsid "$EDITOR" "${ARGS[@]}" >"$LOG" 2>&1 &
CHILD_PID=$!
echo "$CHILD_PID" >"$PIDFILE"

echo
echo "started. wrapper-launched PID (setsid leader): $CHILD_PID  (saved to $PIDFILE)"
echo "follow the log     : tail -f \"$LOG\""
echo "find the real PID  : pgrep -af 'UnrealEditor.*AgentOSBlank'"
echo "kill it cleanly    : pkill -TERM -f 'UnrealEditor.*AgentOSBlank'   # or: kill \$(cat \"$PIDFILE\")"
echo "look for readiness  : grep -E 'LogLoad: .*took|Engine is initialized' \"$LOG\""
