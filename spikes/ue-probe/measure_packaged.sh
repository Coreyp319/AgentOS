#!/usr/bin/env bash
# measure_packaged.sh <label> "<cvar rung string>"
#
# Launch the PACKAGED AgentOSBlank -game build (the wallpaper runtime, NOT the
# editor) offscreen into /Game/AgentOS/CalmWallpaper at the given throttle rung,
# confirm the map loads, sample VRAM, and report the runtime footprint TWO ways:
#   (a) per-process UnrealGame VRAM (nvidia-smi compute-apps) — can UNDERCOUNT
#       Vulkan graphics allocations, so treat as a lower bound.
#   (b) card-used DELTA vs a baseline captured right before launch — the honest
#       total, valid only if the card stays calm (no gen) for the whole run.
#
# Shared-card robustness: a SUSTAINED-CALM pre-flight waits for the card to be
# quiet (free >= MIN_FREE for 3x5s) before capturing the baseline + launching, so
# one of the user's gens can't pollute the measurement. If the card frees, we run;
# if it never frees within PREFLIGHT_BUDGET, we abort cleanly. During sampling we
# also flag CONTAMINATION if free drops sharply (a gen started mid-run).
#
# Launch is via the staged wrapper (paked build has no literal .uproject). PID-safe:
# only ever kills the wrapper PID + its UnrealGame child.
set -uo pipefail

LABEL="${1:?usage: measure_packaged.sh <label> \"<cvar rung>\"}"
RUNG="${2:?need cvar rung string}"

STAGE="$HOME/UnrealProjects/AgentOSBlank/Saved/StagedBuilds/Linux"
WRAPPER="$STAGE/AgentOSBlank.sh"
PROBE="$HOME/Documents/AgentOS/spikes/ue-probe"
LOG="$PROBE/ue_measure_${LABEL}.log"
CSV="$PROBE/vram_${LABEL}.csv"
MIN_FREE="${MIN_FREE:-16000}"
PREFLIGHT_BUDGET="${PREFLIGHT_BUDGET:-900}"   # wait up to 15min for a calm card

[ -f "$WRAPPER" ] || { echo "FAIL: no staged wrapper at $WRAPPER (run package_game.sh)"; exit 1; }

q() { nvidia-smi --query-gpu="$1" --format=csv,noheader,nounits | head -1 | tr -d ' '; }

# --- sustained-calm pre-flight (so a gen can't pollute) ---------------------
echo "[$LABEL] waiting for sustained calm: free >= ${MIN_FREE} MiB x3 (budget ${PREFLIGHT_BUDGET}s)"
calm=0; waited=0
while [ "$waited" -lt "$PREFLIGHT_BUDGET" ]; do
  free=$(q memory.free)
  if [ -n "$free" ] && [ "$free" -ge "$MIN_FREE" ] 2>/dev/null; then calm=$((calm+1)); else calm=0; fi
  [ "$calm" -ge 3 ] && break
  sleep 5; waited=$((waited+5))
done
if [ "$calm" -lt 3 ]; then
  echo "[$LABEL] ABORT: card never went calm within ${PREFLIGHT_BUDGET}s (free=$(q memory.free) MiB)."
  exit 3
fi

BASE_USED=$(q memory.used); BASE_FREE=$(q memory.free)
echo "[$LABEL] baseline (pre-launch): used=${BASE_USED} free=${BASE_FREE} MiB"
echo "[$LABEL] launching packaged -game offscreen @2560x1440; rung: $RUNG"
bash "$WRAPPER" /Game/AgentOS/CalmWallpaper \
  -RenderOffscreen -unattended -ResX=2560 -ResY=1440 -nosound -nosplash \
  -stdout -FullStdOutLogOutput "-ExecCmds=$RUNG" > "$LOG" 2>&1 &
WPID=$!

UE=""
for i in $(seq 1 20); do
  UE=$(pgrep -P "$WPID" 2>/dev/null | head -1)
  [ -n "$UE" ] && break
  kill -0 "$WPID" 2>/dev/null || { echo "[$LABEL] !! wrapper exited before UnrealGame"; tail -8 "$LOG"; exit 4; }
  sleep 1
done
echo "[$LABEL] wrapper=$WPID UnrealGame=${UE:-<not found>}"
( sleep 200; kill -9 "$WPID" ${UE:+$UE} 2>/dev/null ) &

# --- wait for the map to load (prove it's OURS) ---
for i in $(seq 1 40); do
  { [ -n "$UE" ] && ! kill -0 "$UE" 2>/dev/null; } && { echo "[$LABEL] !! UnrealGame exited during load"; break; }
  grep -qiE 'LoadMap Load map complete /Game/AgentOS/CalmWallpaper|Bringing World /Game/AgentOS/CalmWallpaper' "$LOG" 2>/dev/null && { echo "[$LABEL] map loaded at ${i}x2s"; break; }
  sleep 2
done

# --- settle (let Lumen + PSO finish), then sample at 1 Hz ---
sleep 8
echo "n,used_mib,free_mib,util,unrealgame_mib" > "$CSV"
for i in $(seq 1 25); do
  IFS=' ' read -r u f g <<<"$(nvidia-smi --query-gpu=memory.used,memory.free,utilization.gpu --format=csv,noheader,nounits | head -1 | tr -d ',')"
  pmem=$(nvidia-smi --query-compute-apps=used_memory,process_name --format=csv,noheader,nounits 2>/dev/null | awk -F', *' '/UnrealGame/{gsub(/[^0-9]/,"",$1); print $1; exit}')
  echo "$i,$u,$f,$g,${pmem:-0}" >> "$CSV"
  sleep 1
done

echo "[$LABEL] === results over $(($(wc -l < "$CSV")-1)) samples (baseline used=${BASE_USED}) ==="
awk -F, -v base="$BASE_USED" -v basefree="$BASE_FREE" 'NR>1{
  if(mu==""||$2>mu)mu=$2; if(nu==""||$2<nu)nu=$2; su+=$2;
  if(nf==""||$3<nf)nf=$3;
  if(mp==""||$5>mp)mp=$5; if((np==""||$5<np)&&$5>0)np=$5;
  if(mg==""||$4>mg)mg=$4; sgg+=$4; n++
} END{
  meanu=su/n;
  contaminated = (basefree-nf > 3000) ? "YES (a gen likely started mid-run — card delta unreliable)" : "no";
  printf "  UnrealGame proc VRAM : %s-%s MiB  (per-process; lower bound)\n", (np==""?"0":np),(mp==""?"0":mp);
  printf "  card-used DELTA      : mean %.0f MiB over baseline  (min-used delta %.0f)  <-- honest footprint if not contaminated\n", meanu-base, nu-base;
  printf "  card used_MiB        : min=%s mean=%.0f max=%s\n", nu, meanu, mu;
  printf "  card free_MiB        : min=%s\n", nf;
  printf "  GPU util %%           : mean=%.0f max=%s\n", sgg/n, mg;
  printf "  contaminated         : %s\n", contaminated;
}' "$CSV"

kill -9 "$WPID" ${UE:+$UE} 2>/dev/null
echo "[$LABEL] stopped. csv=$CSV log=$LOG"
