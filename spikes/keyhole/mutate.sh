#!/usr/bin/env bash
# mutate.sh — cycle keyhole.json through several states so the Timer-poll's
# live reactivity is observable. Writes with the SAME atomic temp+rename the
# real producer uses (.keyhole.<pid>.tmp -> rename) so the consumer never sees
# a half-written file. Mirrors crates/agentosd/src/feed.rs::write_feed.
#
# Usage:
#   ./mutate.sh                 # cycle once through the script, 3s per state
#   ./mutate.sh <target.json>   # write the cycle to a specific path
#   ./mutate.sh loop            # cycle forever
#   ./mutate.sh loop <path>
#
# SPDX-License-Identifier: MIT
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-$DIR/keyhole.json}"
[ "$TARGET" = "loop" ] && { LOOP=1; TARGET="${2:-$DIR/keyhole.json}"; } || LOOP=0
DWELL="${DWELL:-3}"

write_atomic() {
  # $1 = json body. temp+rename in the same dir == atomic on the consumer side.
  local body="$1"
  local tmp
  tmp="$(dirname "$TARGET")/.keyhole.$$.tmp"
  printf '%s\n' "$body" > "$tmp"
  mv -f "$tmp" "$TARGET"
  echo "[mutate] wrote $(printf '%s' "$body" | tr -d '\n' | head -c 90)…"
}

idle() {
  write_atomic '{
  "schema": 1, "state": "idle", "gateway": "running",
  "floats": { "busy": 0.0, "warm": 0.0, "snag": 0.0 },
  "fleet": { "running": 0, "queued": 0, "snagged": 0 },
  "lease": { "tier": "", "holder": "", "preempt": "" },
  "vram": { "used_mib": 1100, "total_mib": 8192 },
  "residency": [],
  "tokens_per_sec": null
}'
}

working() {
  write_atomic '{
  "schema": 1, "state": "working", "gateway": "running",
  "floats": { "busy": 0.85, "warm": 0.0, "snag": 0.0 },
  "fleet": { "running": 3, "queued": 2, "snagged": 0 },
  "lease": { "tier": "interactive", "holder": "Hermes",
             "preempt": "wallpaper yielded ~1.5GB → qwen2.5 loaded" },
  "vram": { "used_mib": 6240, "total_mib": 8192 },
  "residency": [ { "name": "qwen2.5:14b", "loaded_secs": 240 } ],
  "tokens_per_sec": null
}'
}

needs_you() {
  write_atomic '{
  "schema": 1, "state": "needs_you", "gateway": "running",
  "floats": { "busy": 0.4, "warm": 1.0, "snag": 0.0 },
  "fleet": { "running": 1, "queued": 0, "snagged": 0 },
  "lease": { "tier": "interactive", "holder": "Hermes",
             "preempt": "no contention" },
  "vram": { "used_mib": 5800, "total_mib": 8192 },
  "residency": [ { "name": "qwen2.5:14b", "loaded_secs": 420 } ],
  "tokens_per_sec": null
}'
}

acting() {
  write_atomic '{
  "schema": 1, "state": "acting", "gateway": "running",
  "floats": { "busy": 0.7, "warm": 0.2, "snag": 0.0 },
  "fleet": { "running": 1, "queued": 0, "snagged": 0 },
  "lease": { "tier": "interactive", "holder": "Hermes",
             "preempt": "no contention" },
  "vram": { "used_mib": 5900, "total_mib": 8192 },
  "residency": [ { "name": "qwen2.5:14b", "loaded_secs": 480 } ],
  "tokens_per_sec": null
}'
}

snag() {
  write_atomic '{
  "schema": 1, "state": "snag", "gateway": "running",
  "floats": { "busy": 0.2, "warm": 0.0, "snag": 1.0 },
  "fleet": { "running": 0, "queued": 1, "snagged": 1 },
  "lease": { "tier": "batch", "holder": "ComfyUI",
             "preempt": "preempted by interactive — waiting for GPU" },
  "vram": { "used_mib": 7600, "total_mib": 8192 },
  "residency": [ { "name": "wan2.2", "loaded_secs": 60 } ],
  "tokens_per_sec": null
}'
}

# UNKNOWN: gateway can't be reached. NOTE: state stays whatever was last seen but
# gateway=unknown drives effectiveState -> unknown in the consumer; we also set
# state=unknown for an unambiguous test. Readouts must show em-dashes, NEVER a 0.
unknown() {
  write_atomic '{
  "schema": 1, "state": "unknown", "gateway": "unknown",
  "floats": { "busy": 0.0, "warm": 0.0, "snag": 0.0 },
  "fleet": { "running": -1, "queued": -1, "snagged": -1 },
  "lease": { "tier": "", "holder": "", "preempt": "" },
  "vram": { "used_mib": -1, "total_mib": -1 },
  "residency": [],
  "tokens_per_sec": null
}'
}

cycle() {
  echo "== idle ==";      idle;      sleep "$DWELL"
  echo "== working ==";   working;   sleep "$DWELL"
  echo "== needs_you =="; needs_you; sleep "$DWELL"
  echo "== acting ==";    acting;    sleep "$DWELL"
  echo "== snag ==";      snag;      sleep "$DWELL"
  echo "== UNKNOWN ==";   unknown;   sleep "$DWELL"
}

echo "[mutate] target = $TARGET  (dwell ${DWELL}s)"
if [ "$LOOP" = "1" ]; then
  while true; do cycle; done
else
  cycle
  echo "[mutate] cycle complete; leaving file at UNKNOWN. (run with 'loop' to repeat)"
fi
