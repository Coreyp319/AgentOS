#!/usr/bin/env bash
# wayland_survival_probe.sh — ADR-0029 Open §A probe (the one cheap gate).
#
# QUESTION: can a NATIVE-Wayland UE `-game` proc survive long-lived + INPUT-LESS
# (the wallpaper case)? Epic flags native-Wayland UE as "unusable" — but a wallpaper
# takes no pointer/keyboard input, so it may dodge the (input-related) failure.
#   PASS → Option A (hand-rolled layer-shell host + VulkanRHI swapchain retarget) is a
#          bounded-but-large engine effort worth starting.
#   FAIL → Option A is at risk on this box; fallback "C" (UE→aurora dreamTex) returns.
#
# It FORCES SDL_VIDEODRIVER=wayland (native Wayland, NOT XWayland), loads the Indigo
# Channel, leaves it UNTOUCHED, and monitors survival + GPU-context retention + crash
# signatures for DURATION seconds. Must run from a shell on the live Wayland session
# (this one is: WAYLAND_DISPLAY set). It is NOT a layer-shell test (that's the §A build);
# it is the survival PROXY that justifies building it.
#
#   DURATION=300 SAMPLE=20 bash spikes/ue-probe/wayland_survival_probe.sh
#   (run via Bash run_in_background:true; kill stragglers with
#    pkill -9 -f '[B]inaries/Linux/UnrealEditor')

set -uo pipefail
DURATION="${DURATION:-300}"          # seconds of stable survival required for a PASS
SAMPLE="${SAMPLE:-20}"               # poll interval
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDITOR="${HOME}/UnrealEngine/Engine/Binaries/Linux/UnrealEditor"
PROJECT="${HOME}/UnrealProjects/AgentOSBlank/AgentOSBlank.uproject"
MAP="${MAP:-/Game/AgentOS/CalmWallpaper}"
LOG="${SCRIPT_DIR}/ue_wayland_probe.log"

[ -x "$EDITOR" ]  || { echo "FAIL: editor not found: $EDITOR"; exit 2; }
[ -n "${WAYLAND_DISPLAY:-}" ] || { echo "FAIL: no WAYLAND_DISPLAY — not on a live Wayland session (probe needs a real GL/Wayland context)"; exit 2; }

# Force NATIVE wayland; input-less = we launch it and never focus/interact.
SDL_VIDEODRIVER=wayland "$EDITOR" "$PROJECT" "$MAP" \
  -game -windowed -ResX=1280 -ResY=720 -nosplash -nosound -stdout -FullStdOutLogOutput \
  "-ExecCmds=r.VolumetricFog 1, t.MaxFPS 30" > "$LOG" 2>&1 &
PID=$!
echo "launched native-Wayland UE pid=$PID (SDL_VIDEODRIVER=wayland); probing survival for ${DURATION}s..."

# --- boot gate ---
boot=0
for _ in $(seq 1 30); do
  if grep -qa "LoadMap Load map complete" "$LOG" 2>/dev/null; then boot=1; break; fi
  kill -0 "$PID" 2>/dev/null || { echo "=== FAIL: died DURING boot ==="; tail -10 "$LOG"; exit 1; }
  sleep 2
done
[ "$boot" = 1 ] || { echo "=== FAIL: never finished boot in 60s (hung at startup) ==="; kill -9 "$PID" 2>/dev/null; tail -10 "$LOG"; exit 1; }
DRIVER="$(grep -a 'Using SDL video driver' "$LOG" | head -1)"
echo "booted. ${DRIVER}"
echo "$DRIVER" | grep -qi "wayland" || echo "WARN: NOT on the native-wayland SDL driver — XWayland fallback? (a wallpaper-layer needs native wayland)"

# --- survival loop ---
t=0; verdict="PASS"; reason="survived ${DURATION}s native-Wayland, input-less, GPU-context retained"
peak=0
while [ "$t" -lt "$DURATION" ]; do
  if ! kill -0 "$PID" 2>/dev/null; then verdict="FAIL"; reason="process EXITED at ~${t}s (the 'unusable' failure may bite a long-lived proc)"; break; fi
  if grep -qaE "Signal 11|Fatal error|SIGSEGV|Assertion failed|Engine exit requested" "$LOG" 2>/dev/null; then verdict="FAIL"; reason="CRASH signature in log at ~${t}s"; break; fi
  vram="$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits 2>/dev/null | awk -v p="$PID" -F',' '{gsub(/ /,"",$1); if($1==p){gsub(/ /,"",$2); print $2}}')"
  if [ -z "${vram:-}" ]; then verdict="FAIL"; reason="UE lost its GPU allocation at ~${t}s (lost Vulkan context)"; break; fi
  [ "${vram:-0}" -gt "$peak" ] && peak="$vram"
  util="$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1)"
  echo "  t=${t}s : alive, UE vram=${vram}MiB, card util=${util:-?}%"
  sleep "$SAMPLE"; t=$((t+SAMPLE))
done

echo "=== ${verdict}: ${reason} (peak UE VRAM ${peak}MiB) ==="
echo "--- log tail ---"; tail -6 "$LOG"
# clean up the probe instance (don't leave it holding the card)
if kill -0 "$PID" 2>/dev/null; then kill -TERM "$PID" 2>/dev/null; sleep 2; kill -9 "$PID" 2>/dev/null; fi
[ "$verdict" = "PASS" ] && exit 0 || exit 1
