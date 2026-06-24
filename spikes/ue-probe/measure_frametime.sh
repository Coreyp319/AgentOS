#!/usr/bin/env bash
# measure_frametime.sh <label> "<cvar rung>"
#
# GPU-TIME (not VRAM) cost of the packaged wallpaper runtime at a given rung — the
# "performant vignettes" metric. Whole-GPU util is polluted by other tenants
# (desktop/haruna/gens), so the PRIMARY signal here is per-process SM% via
# `nvidia-smi pmon` (UnrealGame's own GPU-time share). The CSV profiler's
# FrameTime/GPUTime ms is captured as a bonus when available.
#
# Headroom read: if UE holds X% SM at a 30fps cap, a co-resident gen has ~(100-X)%
# of GPU-time available (modulo scheduler). Same calm-gate + PID-safe machinery as
# measure_packaged.sh.
set -uo pipefail

LABEL="${1:?usage: measure_frametime.sh <label> \"<cvar rung>\"}"
RUNG="${2:?need cvar rung string}"

STAGE="$HOME/UnrealProjects/AgentOSBlank/Saved/StagedBuilds/Linux"
WRAPPER="$STAGE/AgentOSBlank.sh"
PROBE="$HOME/Documents/AgentOS/spikes/ue-probe"
LOG="$PROBE/ue_frametime_${LABEL}.log"
PMON="$PROBE/pmon_${LABEL}.txt"
CSVDIR="$HOME/UnrealProjects/AgentOSBlank/Saved/Profiling/CSV"
MIN_FREE="${MIN_FREE:-16000}"
PREFLIGHT_BUDGET="${PREFLIGHT_BUDGET:-2400}"

[ -f "$WRAPPER" ] || { echo "FAIL: no staged wrapper"; exit 1; }
q(){ nvidia-smi --query-gpu="$1" --format=csv,noheader,nounits | head -1 | tr -d ' '; }

echo "[$LABEL] waiting for sustained calm: free >= ${MIN_FREE} x3"
calm=0; waited=0
while [ "$waited" -lt "$PREFLIGHT_BUDGET" ]; do
  f=$(q memory.free); { [ -n "$f" ] && [ "$f" -ge "$MIN_FREE" ] 2>/dev/null; } && calm=$((calm+1)) || calm=0
  [ "$calm" -ge 3 ] && break; sleep 5; waited=$((waited+5))
done
[ "$calm" -ge 3 ] || { echo "[$LABEL] ABORT: card never calm in ${PREFLIGHT_BUDGET}s"; exit 3; }

# CSV profiler ON via -ExecCmds (continuous writes so a kill still leaves data).
EXEC="$RUNG, csv.ContinuousWrites 1, CsvProfile Start"
echo "[$LABEL] launching; rung+profiler: $EXEC"
bash "$WRAPPER" /Game/AgentOS/CalmWallpaper \
  -RenderOffscreen -unattended -ResX=2560 -ResY=1440 -nosound -nosplash \
  -csvGpuStats -stdout -FullStdOutLogOutput "-ExecCmds=$EXEC" > "$LOG" 2>&1 &
WPID=$!
UE=""
for i in $(seq 1 20); do UE=$(pgrep -P "$WPID" 2>/dev/null | head -1); [ -n "$UE" ] && break
  kill -0 "$WPID" 2>/dev/null || { echo "[$LABEL] !! wrapper died"; tail -8 "$LOG"; exit 4; }; sleep 1; done
echo "[$LABEL] UnrealGame=$UE"
( sleep 200; kill -9 "$WPID" ${UE:+$UE} 2>/dev/null ) &

for i in $(seq 1 30); do
  grep -qiE 'LoadMap Load map complete /Game/AgentOS/CalmWallpaper' "$LOG" 2>/dev/null && { echo "[$LABEL] map loaded"; break; }
  { [ -n "$UE" ] && ! kill -0 "$UE" 2>/dev/null; } && { echo "[$LABEL] !! UE exited during load"; break; }
  sleep 2
done
sleep 6   # let it reach steady-state framerate

# PRIMARY: per-process SM% over ~22s
echo "[$LABEL] sampling per-process SM% (nvidia-smi pmon)..."
nvidia-smi pmon -c 22 -s u -d 1 > "$PMON" 2>/dev/null || echo "[$LABEL] pmon failed (perm?) — will fall back to whole-GPU util"
echo "[$LABEL] === GPU-time share (per-process SM%) ==="
awk -v p="$UE" '$2==p && $4 ~ /^[0-9]+$/ {s+=$4; if($4>mx)mx=$4; n++} END{
  if(n) printf "  UnrealGame SM%%: mean=%.0f  max=%d  (over %d x1s samples) <-- GPU-time share\n", s/n, mx, n;
  else  print  "  (no pmon rows for UE pid — see whole-GPU util fallback below)"}' "$PMON"
# fallback / context: whole-GPU util
IFS=' ' read -r wu <<<"$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | tr -d ' ')"
echo "  whole-GPU util now: ${wu}%  (includes desktop + any other tenant)"

# BONUS: CSV profiler ms/frame
NEWCSV=$(ls -t "$CSVDIR"/*.csv 2>/dev/null | head -1)
if [ -n "$NEWCSV" ] && [ -s "$NEWCSV" ]; then
  echo "[$LABEL] === CSV profiler ($(basename "$NEWCSV")) ==="
  awk -F, 'NR==1{for(i=1;i<=NF;i++){h=$i; gsub(/^ +| +$/,"",h); if(h=="FrameTime")ft=i; if(h=="GPUTime")gt=i}}
    NR>1 && ft{ if($ft+0>0){sf+=$ft; nf++} if(gt && $gt+0>0){sg+=$gt; ng++} }
    END{ if(nf)printf "  FrameTime: mean=%.2f ms (%.0f fps)\n", sf/nf, 1000/(sf/nf);
         if(ng)printf "  GPUTime  : mean=%.2f ms/frame\n", sg/ng;
         if(!nf)print "  (no FrameTime column parsed)"}' "$NEWCSV"
else
  echo "[$LABEL] (no CSV profiler output — SM% above is the GPU-time signal)"
fi

kill -9 "$WPID" ${UE:+$UE} 2>/dev/null
echo "[$LABEL] stopped. log=$LOG pmon=$PMON"
