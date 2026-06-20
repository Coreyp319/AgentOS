#!/usr/bin/env bash
# sample_vram.sh — sample GPU VRAM + utilization at 1 Hz for N seconds, log it,
# then print min/median/max used_MiB and min free_MiB.
#
# Usage:
#   ./sample_vram.sh <seconds>
#
# Example:
#   ./sample_vram.sh 60          # sample for 60s while a UE rung is running
#
# Output:
#   - a timestamped CSV-ish log under this directory: vram_sample_<ts>.log
#     columns: epoch_s, used_MiB, free_MiB, util_pct
#   - a summary line block on stdout (min/median/max used, min free, mean util)
#
# Pure bash + nvidia-smi + sort/awk. Samples once per second for <seconds>
# samples. NOTE: this READS the GPU (nvidia-smi query) — it launches no GPU work.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SECONDS_TO_SAMPLE="${1:-}"
if [[ -z "$SECONDS_TO_SAMPLE" || ! "$SECONDS_TO_SAMPLE" =~ ^[0-9]+$ || "$SECONDS_TO_SAMPLE" -lt 1 ]]; then
  echo "usage: $0 <seconds>   (positive integer)" >&2
  exit 2
fi
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "error: nvidia-smi not found on PATH" >&2
  exit 1
fi

TS="$(date +%Y%m%d_%H%M%S)"
LOG="${SCRIPT_DIR}/vram_sample_${TS}.log"

echo "# epoch_s,used_MiB,free_MiB,util_pct" >"$LOG"
echo "sampling VRAM for ${SECONDS_TO_SAMPLE}s at 1 Hz -> $LOG"

QUERY="memory.used,memory.free,utilization.gpu"

i=0
while [[ "$i" -lt "$SECONDS_TO_SAMPLE" ]]; do
  # noheader,nounits => raw numbers; format: "used, free, util"
  line="$(nvidia-smi --query-gpu="$QUERY" --format=csv,noheader,nounits 2>/dev/null \
            | head -n1 | tr -d ' ')"
  if [[ -n "$line" ]]; then
    echo "$(date +%s),${line}" >>"$LOG"
  fi
  i=$((i + 1))
  # don't sleep after the final sample
  if [[ "$i" -lt "$SECONDS_TO_SAMPLE" ]]; then
    sleep 1
  fi
done

# ---- summary --------------------------------------------------------------
# Strip the header + comment line, then compute stats with awk + sort for median.
DATA="$(grep -v '^#' "$LOG" || true)"
N="$(printf '%s\n' "$DATA" | grep -c . || true)"

if [[ "$N" -eq 0 ]]; then
  echo "no samples collected (is nvidia-smi returning data?)" >&2
  exit 1
fi

# used_MiB stats
used_sorted="$(printf '%s\n' "$DATA" | awk -F, '{print $2}' | sort -n)"
used_min="$(printf '%s\n' "$used_sorted" | head -n1)"
used_max="$(printf '%s\n' "$used_sorted" | tail -n1)"
used_median="$(printf '%s\n' "$used_sorted" \
  | awk '{a[NR]=$1} END{ if (NR%2) print a[(NR+1)/2]; else printf "%.1f", (a[NR/2]+a[NR/2+1])/2 }')"

# free_MiB min
free_min="$(printf '%s\n' "$DATA" | awk -F, '{print $3}' | sort -n | head -n1)"

# util mean
util_mean="$(printf '%s\n' "$DATA" | awk -F, '{s+=$4; n++} END{ if(n) printf "%.1f", s/n }')"

echo
echo "=== VRAM sample summary ($N samples over ~${SECONDS_TO_SAMPLE}s) ==="
echo "used_MiB   min=${used_min}  median=${used_median}  max=${used_max}"
echo "free_MiB   min=${free_min}"
echo "util_pct   mean=${util_mean}"
echo "log: $LOG"
