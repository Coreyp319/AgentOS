#!/usr/bin/env bash
# probe_native_wayland.sh — the ONE cheap probe that decides whether ADR-0023's
# Option A (live, authoritative UE-as-wallpaper) is tractable or dead on this box.
#
# WHY: Corey governed the surface fork to Option A (docs/design/0023-phaseb-surface-
# strategy.md, "Human decision of record"). A needs UE to present its own pixels on a
# wlr-layer-shell BACKGROUND surface — which only exists under NATIVE-Wayland UE. Epic
# documents native-Wayland UE as "unusable" (broken POINTER input). But a wallpaper
# takes NO pointer input, so the bug *may* not matter. That is undecidable at the desk —
# this probe settles it: does a packaged UE -game build come up native-Wayland and
# SURVIVE a sustained onscreen run on this Plasma 6 / KWin 6.6.5 session?
#
#   PASS  → UE runs native-Wayland, onscreen, stable → A's hand-rolled layer-shell +
#           VulkanRHI swapchain retarget becomes a bounded (large) engine effort.
#   FAIL  → UE refuses Wayland / falls back to XWayland / crashes / hangs → A is dead on
#           this box, and Option C (UE-off-surface → aurora dreamTex) returns with data.
#
# !!! RUN THIS IN A LIVE KONSOLE ON THE PLASMA SESSION — NOT from a detached/agent shell.
#     Offscreen/agent shells have no GL context; the scene-graph render thread never
#     starts (blank, exit 0 = a FALSE negative). This is onscreen ON PURPOSE.
#
# Usage:  ./probe_native_wayland.sh [--seconds N] [--rung REDUCED|FLOOR|FULL]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GAME_WRAPPER="${HOME}/UnrealProjects/AgentOSBlank/Saved/StagedBuilds/Linux/AgentOSBlank.sh"
WATCH_SECONDS=60
RUNG="REDUCED"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --seconds) WATCH_SECONDS="${2:?}"; shift 2 ;;
    --rung)    RUNG="${2:?}"; shift 2 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

case "$RUNG" in
  FULL)    EXEC="t.MaxFPS 30" ;;
  REDUCED) EXEC="r.ScreenPercentage 70, sg.GlobalIlluminationQuality 2, sg.ShadowQuality 2, sg.ReflectionQuality 2, t.MaxFPS 30" ;;
  FLOOR)   EXEC="r.ScreenPercentage 50, sg.GlobalIlluminationQuality 0, sg.ShadowQuality 0, sg.ReflectionQuality 0, r.Streaming.PoolSize 512, r.Streaming.LimitPoolSizeToVRAM 1, t.MaxFPS 5" ;;
  *) echo "unknown --rung: $RUNG (FULL|REDUCED|FLOOR)" >&2; exit 2 ;;
esac

[[ -x "$GAME_WRAPPER" ]] || { echo "error: packaged build wrapper not found/executable: $GAME_WRAPPER" >&2; echo "  (cook it first: ./package_game.sh)"; exit 1; }

# --- session sanity: this MUST be a live Wayland session -----------------------
if [[ "${XDG_SESSION_TYPE:-}" != "wayland" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  echo "REFUSING: no Wayland session detected (XDG_SESSION_TYPE='${XDG_SESSION_TYPE:-unset}', WAYLAND_DISPLAY='${WAYLAND_DISPLAY:-unset}')."
  echo "Run this in a konsole ON the Plasma 6 Wayland session — that is the whole point."
  exit 3
fi

# --- VRAM pre-flight: a packaged -game is ~1GB, but don't launch into an OOM ----
read -r FREE_MIB < <(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null || echo 0)
echo "free VRAM: ${FREE_MIB} MiB"
if (( FREE_MIB < 2500 )); then
  echo "WARNING: <2.5GB free — a heavy gen may be resident. Proceeding (UE -game is light), but watch for a crash."
fi

TS="$(date +%Y%m%d_%H%M%S)"
LOG="${SCRIPT_DIR}/probe_wayland_${TS}.log"
echo "=== native-Wayland UE boot-survival probe ==="
echo "rung=${RUNG}  watch=${WATCH_SECONDS}s  log=${LOG}"
echo "forcing SDL_VIDEODRIVER=wayland, ONSCREEN (windowed 1280x720), map=GameDefaultMap (CalmWallpaper)"
echo

# Launch ONSCREEN + native-Wayland. NB: -ExecCmds is re-quoted by UE — NO inner quotes.
SDL_VIDEODRIVER=wayland \
  "$GAME_WRAPPER" \
  -windowed -ResX=1280 -ResY=720 -WinX=80 -WinY=80 \
  -ExecCmds="${EXEC}" \
  >"$LOG" 2>&1 &
PID=$!
echo "launched PID=$PID — a UE window SHOULD appear on your screen now."
echo "watching ${WATCH_SECONDS}s (Ctrl-C to stop the watch; the proc keeps running)…"

ALIVE=1
for ((t=0; t<WATCH_SECONDS; t+=5)); do
  sleep 5
  if ! kill -0 "$PID" 2>/dev/null; then ALIVE=0; echo "  [t=${t}s] PROCESS EXITED (see log)"; break; fi
  read -r U M < <(nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits 2>/dev/null || echo "? ?")
  echo "  [t=${t}s] alive  util=${U}%  card-used=${M}MiB"
done

echo
echo "=== verdict heuristics (confirm with your EYES — did a real UE window render?) ==="
# 1) Did UE actually select the Wayland SDL driver, or silently fall back to XWayland/x11?
echo "--- SDL/Wayland driver selection (grep) ---"
grep -iE 'wayland|xwayland|sdl.*video|video.*driver|x11' "$LOG" | head -12 || echo "  (no driver line found — check the log directly)"
echo "--- crash / device-loss / Vulkan markers (grep) ---"
grep -iE 'SIGABRT|SIGSEGV|device.?lost|VK_ERROR|out of memory|Fatal|Assertion failed|LowLevelFatalError' "$LOG" | head -12 || echo "  (none — good sign)"
echo
if (( ALIVE == 1 )) && kill -0 "$PID" 2>/dev/null; then
  echo "RESULT: process SURVIVED ${WATCH_SECONDS}s. → look at the screen:"
  echo "  - real UE window rendering + Wayland driver line above + no crash markers = PASS (A is tractable)."
  echo "  - blank/black window, or driver fell back to XWayland/x11 = NOT native-Wayland = lean FAIL."
  echo "  PID=$PID still running. Kill with:  kill $PID   (or: pkill -f UnrealGame)"
else
  echo "RESULT: process DID NOT survive → lean FAIL (native-Wayland UE unstable on this box). Read $LOG."
fi
echo
echo "Record the outcome in docs/design/0023-phaseb-surface-strategy.md (Human decision of record)."
