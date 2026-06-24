#!/usr/bin/env bash
# coexist_safe.sh — SAFE Phase-A coexistence test: throttled packaged UE wallpaper
# (FLOOR rung) + a resident small Ollama model, both MINE, on a calm card.
#
# Deliberately does NOT touch the user's ComfyUI/lucid (their gens own that lane).
# The UE + heavy-Wan-gen cliff is observed live, not forced here. This proves the
# curated-coexistence case: a yielding UE wallpaper co-resident with a vision/LLM
# model, no OOM, both alive. Loads moondream (~1.7GB) via ollama_load_small.sh,
# samples the combined footprint, checks liveness, then cleans up (stops UE, evicts
# the model). PID-safe; calm-gated.
set -uo pipefail

STAGE="$HOME/UnrealProjects/AgentOSBlank/Saved/StagedBuilds/Linux"
WRAPPER="$STAGE/AgentOSBlank.sh"
PROBE="$HOME/Documents/AgentOS/spikes/ue-probe"
LOG="$PROBE/ue_coexist.log"
OLLAMA="${OLLAMA_HOST:-127.0.0.1:11434}"
MODEL="${MODEL:-moondream:latest}"
FLOOR="r.ScreenPercentage 50, sg.GlobalIlluminationQuality 0, sg.ShadowQuality 0, sg.ReflectionQuality 0, sg.PostProcessQuality 0, r.Streaming.PoolSize 512, r.Streaming.LimitPoolSizeToVRAM 1, t.MaxFPS 5"
MIN_FREE="${MIN_FREE:-16000}"
PREFLIGHT_BUDGET="${PREFLIGHT_BUDGET:-2400}"

q(){ nvidia-smi --query-gpu="$1" --format=csv,noheader,nounits | head -1 | tr -d ' '; }
procmem(){ nvidia-smi --query-compute-apps=used_memory,process_name --format=csv,noheader,nounits 2>/dev/null | awk -F', *' -v pat="$1" '$0 ~ pat {gsub(/[^0-9]/,"",$1); print $1; exit}'; }

[ -f "$WRAPPER" ] || { echo "FAIL: no staged wrapper"; exit 1; }

echo "=== sustained-calm pre-flight (free >= ${MIN_FREE} x3) ==="
calm=0; waited=0
while [ "$waited" -lt "$PREFLIGHT_BUDGET" ]; do
  f=$(q memory.free); { [ -n "$f" ] && [ "$f" -ge "$MIN_FREE" ] 2>/dev/null; } && calm=$((calm+1)) || calm=0
  [ "$calm" -ge 3 ] && break; sleep 5; waited=$((waited+5))
done
[ "$calm" -ge 3 ] || { echo "ABORT: card never calm in ${PREFLIGHT_BUDGET}s"; exit 3; }

BASE=$(q memory.used)
echo "=== (0) baseline: card used=${BASE} MiB free=$(q memory.free) ==="

echo "=== (1) launch throttled UE (FLOOR) offscreen, keep resident ==="
bash "$WRAPPER" /Game/AgentOS/CalmWallpaper \
  -RenderOffscreen -unattended -ResX=2560 -ResY=1440 -nosound -nosplash \
  -stdout -FullStdOutLogOutput "-ExecCmds=$FLOOR" > "$LOG" 2>&1 &
WPID=$!
UE=""
for i in $(seq 1 20); do UE=$(pgrep -P "$WPID" 2>/dev/null | head -1); [ -n "$UE" ] && break
  kill -0 "$WPID" 2>/dev/null || { echo "!! wrapper died"; tail -8 "$LOG"; exit 4; }; sleep 1; done
( sleep 240; kill -9 "$WPID" ${UE:+$UE} 2>/dev/null ) &
for i in $(seq 1 30); do grep -qiE 'LoadMap Load map complete /Game/AgentOS/CalmWallpaper' "$LOG" 2>/dev/null && break
  { [ -n "$UE" ] && ! kill -0 "$UE" 2>/dev/null; } && { echo "!! UE exited during load"; break; }; sleep 2; done
sleep 6
UE_USED=$(q memory.used); UE_MEM=$(procmem UnrealGame)
echo "    UnrealGame=$UE  proc_VRAM=${UE_MEM:-?} MiB  | card used=${UE_USED} (UE delta=$((UE_USED-BASE)))"

echo "=== (2) load small Ollama model ($MODEL) resident ==="
MODEL="$MODEL" bash "$PROBE/ollama_load_small.sh" 2>&1 | grep -E 'size_vram|RESIDENT|generate done|NO models' || true
sleep 2
BOTH_USED=$(q memory.used); BOTH_FREE=$(q memory.free); OLL_MEM=$(procmem 'ollama|llama')
echo "    card used=${BOTH_USED}  free=${BOTH_FREE}  | ollama proc_VRAM=${OLL_MEM:-?} MiB (ollama delta=$((BOTH_USED-UE_USED)))"
echo "    ollama /api/ps:"; curl -s "http://$OLLAMA/api/ps" 2>/dev/null | python3 -c 'import sys,json;[print("      ",m["name"],"size_vram=%.2fGiB"%(m.get("size_vram",0)/1024**3)) for m in json.load(sys.stdin).get("models",[])]' 2>/dev/null || echo "      (ps parse failed)"

echo "=== (3) liveness / OOM checks (both resident) ==="
dmesg 2>/dev/null | tail -n 30 | grep -iE 'oom|killed process|out of memory|xid|nvrm' && echo "    !! dmesg shows GPU/OOM events" || echo "    dmesg: clean"
{ [ -n "$UE" ] && kill -0 "$UE" 2>/dev/null; } && echo "    UE: alive" || echo "    UE: DOWN (crashed?)"
grep -iE 'device lost|out of memory|VK_ERROR|fatal' "$LOG" 2>/dev/null | tail -3 || echo "    UE log: clean"
curl -s "http://$OLLAMA/api/ps" >/dev/null 2>&1 && echo "    Ollama: alive" || echo "    Ollama: DOWN"

echo "=== (4) verdict ==="
echo "    baseline=${BASE}  UE-resident=${UE_USED}  UE+Ollama=${BOTH_USED}  free@peak=${BOTH_FREE}"
echo "    UE-FLOOR footprint ~$((UE_USED-BASE)) MiB | +Ollama ~$((BOTH_USED-UE_USED)) MiB | headroom left ${BOTH_FREE} MiB"

echo "=== (5) cleanup: stop UE, evict $MODEL ==="
kill -9 "$WPID" ${UE:+$UE} 2>/dev/null
curl -s "http://$OLLAMA/api/generate" -d "{\"model\":\"$MODEL\",\"keep_alive\":0}" >/dev/null 2>&1 && echo "    evicted $MODEL" || echo "    (ollama evict skipped)"
echo "    done. log=$LOG"
