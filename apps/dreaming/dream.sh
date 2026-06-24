#!/usr/bin/env bash
# dream.sh — AgentOS overnight "dreaming" runner (Surface A cache-fill, ADR-0009/0010).
#
# A D-Bus *client* of the agentosd coordinator (NOT the daemon). It wires the validated
# ComfyUI backend (comfy_client.py) to the VRAM lease so dreaming generation is
# coordinator-governed, the way ADR-0009 §3 / ADR-0010 require:
#
#   1. ask agentosd to Spawn + OWN ComfyUI under the *batch* lease. Admission is
#      predict-before-load: if the GPU is busy (a model loaded, free < estimate) the
#      coordinator refuses — dreaming is overnight-only and fails open (ADR-0003), it
#      never fights live inference for VRAM.
#   2. wait for ComfyUI to be ready,
#   3. generate ONE short, SFW, muted clip via comfy_client.py (the one backend, ADR-0001),
#   4. atomically publish it to the dream cache (the Surface A dream-as-texture source),
#   5. release the lease → agentosd SIGKILLs the owned ComfyUI → VRAM reclaimed.
#
# On preemption (interactive inference arrives mid-generation) agentosd SIGKILLs the owned
# ComfyUI under us; the in-flight clip is lost (a cache artifact only, no desktop state) and
# the job simply requeues next window. A trap releases the lease on any exit so a crash
# can't leak the lease (and thus ComfyUI's ~17 GB).
#
# SFW by contract (ADR-0009 §2): a fixed SFW prompt + negative; NSFW is never on this path.
# Muted by contract: these models emit no audio track, and the publish copies video only.
#
# Test seams (env overrides, so the orchestration is provable without a real GPU run):
#   DREAM_LAUNCHER       argv agentosd Spawns as the owned holder (default: start-comfyui.sh)
#   DREAM_READY_CMD      readiness probe (default: curl ComfyUI /system_stats)
#   DREAM_GEN_CMD        generation step (default: comfy_client.py run-template …)
#   DREAM_CACHE_DIR      where clips are published (default: $XDG_CACHE_HOME/agentos/dreams)
#   DREAM_EST_MIB        admission estimate (default 9000 ≈ Wan 5B; use 17000 for 14B)
set -u

# --- coordinator (must match crates/agentosd/src/lease.rs) ---
COORD_NAME="org.agentos.Coordinator1"
COORD_PATH="/org/agentos/Coordinator1"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMFY_ROOT="${COMFY_ROOT:-$HOME/ComfyUI}"
COMFY_HOST="${COMFY_HOST:-127.0.0.1:8188}"
OUTPUT_DIR="$COMFY_ROOT/output"

EST_MIB="${DREAM_EST_MIB:-9000}"
CACHE_DIR="${DREAM_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/agentos/dreams}"
TEMPLATE="${DREAM_TEMPLATE:-$COMFY_ROOT/blueprints/Text to Video (Wan 2.2).json}"
READY_TIMEOUT="${DREAM_READY_TIMEOUT:-180}"

# Calm, abstract, SFW — the clip is a texture the shader warps, not a literal scene.
PROMPT="${DREAM_PROMPT:-a calm slow aurora drifting over dark rolling hills, soft gradients, gentle abstract motion, serene, no people}"
NEG="${DREAM_NEG:-text, watermark, people, faces, nsfw, harsh flicker, fast cuts, jitter}"
LENGTH="${DREAM_LENGTH:-49}"; STEPS="${DREAM_STEPS:-6}"
WIDTH="${DREAM_WIDTH:-512}"; HEIGHT="${DREAM_HEIGHT:-512}"

log() { echo "[dream $(date +%H:%M:%S)] $*"; }

# agentosd owns ComfyUI via the daemon-owned "comfyui" launch PROFILE (ADR-0013 A2): the
# daemon resolves the profile to an absolute command itself — callers no longer pass a binary
# (closed the Spawn-arbitrary-argv RCE). start-comfyui.sh execs python, so the owned PID *is*
# python → SIGKILL frees the CUDA context. Override DREAM_PROFILE/DREAM_PARAMS for tests.
PROFILE="${DREAM_PROFILE:-comfyui}"
# shellcheck disable=SC2206
PARAMS=(${DREAM_PARAMS:-})

coord() { busctl --user call "$COORD_NAME" "$COORD_PATH" "$COORD_NAME" "$@"; }

TOKEN=""
cleanup() {
  if [ -n "$TOKEN" ] && [ "$TOKEN" != "0" ]; then
    log "releasing lease token $TOKEN (agentosd SIGKILLs the owned ComfyUI)"
    coord Release t "$TOKEN" >/dev/null 2>&1 || true
    TOKEN=""
  fi
}
trap cleanup EXIT INT TERM

# 1. Acquire the batch lease by having agentosd Spawn + own ComfyUI (predict-before-load).
log "requesting batch lease (est ${EST_MIB}M) → Spawn profile=$PROFILE ${PARAMS[*]}"
out="$(coord Spawn susas batch "$EST_MIB" "$PROFILE" "${#PARAMS[@]}" "${PARAMS[@]}" 2>&1)" || {
  log "coordinator unreachable ($out) — skipping (fail-open, ADR-0003)"; exit 0; }
read -r _sig granted token _rest <<<"$out"
if [ "${granted:-false}" != "true" ]; then
  log "not granted — ${out#bts false 0 } — skipping (dreaming is overnight-only)"; exit 0
fi
TOKEN="$token"
log "lease GRANTED (token $TOKEN); agentosd owns ComfyUI"

# 2. Wait for ComfyUI to be ready.
ready() {
  if [ -n "${DREAM_READY_CMD:-}" ]; then eval "$DREAM_READY_CMD"; else
    curl -sf -m2 "http://$COMFY_HOST/system_stats" >/dev/null 2>&1; fi
}
log "waiting for ComfyUI (≤${READY_TIMEOUT}s)…"
deadline=$((SECONDS + READY_TIMEOUT))
until ready; do
  if [ "$SECONDS" -ge "$deadline" ]; then log "ComfyUI not ready in time — aborting (requeue)"; exit 1; fi
  sleep 1
done
log "ComfyUI ready"

# 3. Generate one SFW clip. Record start so we can pick the clip this run produced.
start_epoch="$(date +%s)"
log "generating: \"$PROMPT\" (${WIDTH}x${HEIGHT}, len $LENGTH, steps $STEPS)"
if [ -n "${DREAM_GEN_CMD:-}" ]; then
  eval "$DREAM_GEN_CMD" || { log "generation failed — aborting (requeue)"; exit 1; }
else
  python3 "$HERE/comfy_client.py" run-template "$TEMPLATE" \
    --prompt "$PROMPT" --negative "$NEG" \
    --length "$LENGTH" --steps "$STEPS" --width "$WIDTH" --height "$HEIGHT" \
    --out-prefix dream \
    || { log "generation failed — aborting (requeue)"; exit 1; }
fi

# Newest video written during this run (matches comfy_client's own fallback).
clip="$(find "$OUTPUT_DIR" -type f \( -iname '*.mp4' -o -iname '*.webm' -o -iname '*.mkv' \) \
        -newermt "@$((start_epoch - 2))" -printf '%T@ %p\n' 2>/dev/null \
        | sort -nr | head -1 | cut -d' ' -f2-)"
if [ -z "$clip" ] || [ ! -f "$clip" ]; then
  log "no clip produced — aborting (requeue)"; exit 1
fi
log "generated $clip"

# 4. Atomically publish to the dream cache (temp + rename; never a half-written read).
mkdir -p "$CACHE_DIR"
ts="$(date +%s)"
dest="$CACHE_DIR/dream-$ts.mp4"
tmp="$CACHE_DIR/.dream-$$.tmp.mp4"
cp -f "$clip" "$tmp" && mv -f "$tmp" "$dest"
# 'current.mp4' = the pointer the Surface A consumer reads; atomic via rename.
ln -sf "$(basename "$dest")" "$CACHE_DIR/.current.lnk" && mv -Tf "$CACHE_DIR/.current.lnk" "$CACHE_DIR/current.mp4"
log "published → $dest  (current.mp4 → $(basename "$dest"))"

# 5. Release happens in the trap → agentosd SIGKILLs the owned ComfyUI → VRAM reclaimed.
log "done"
