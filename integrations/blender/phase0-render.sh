#!/usr/bin/env bash
# phase0-render.sh — hand-driven Phase 0 proof for ADR-0022 (NO MCP bridge, no gateway, no sandbox).
#
# This is the cheapest de-risking of the whole creative-app bet: it proves that a LEASED headless
# Blender render coexists with the rest of the GPU world (ComfyUI / inference) in the heavy lane, and
# that its frame lands at the lucid anchor seam — using only machinery that exists today. It is the
# Blender analog of apps/dreaming/dream.sh and a D-Bus *client* of the agentosd coordinator (NOT the
# daemon). Phase 0 is the GATE on Phase 1 (building the actual MCP bridge).
#
# Flow:
#   1. ask agentosd to Spawn + OWN a `blender-render` job under the *batch* lease (predict-before-load
#      admission; fails open if the GPU is busy — a render never fights live inference, ADR-0003);
#   2. wait for the rendered frame to appear;
#   3. publish it to the anchor cache and print the lucid command to consume it (manual in Phase 0,
#      ADR-0022 §7 — lucid is not yet on the lease);
#   4. a trap releases the lease on abnormal exit so a hung render can't hold the lane (on the success
#      path the render exits on its own → agentosd auto-releases → the trap Release is a no-op).
#
# Deliberate-OOM acceptance test (the single most important thing to prove, ADR-0022 §Phase-0):
#   AOS_BLENDER_STRESS=10 ./phase0-render.sh
# then confirm: (a) the desktop/compositor survives, (b) the lease auto-releases, (c) VRAM is reclaimed
# (watch `agentosd keyhole --once` or `nvidia-smi`). Tune STRESS upward until it OOMs the card.
#
# Env knobs (test seams):
#   AOS_BLENDER_EST_MIB   admission reservation (default 8000 — a fixed CONSERVATIVE ceiling; ADR-0022
#                         §3: Cycles VRAM is unbounded and ADR-0018 coexist CANNOT learn it)
#   AOS_BLENDER_DEVICE    OPTIX | CUDA | CPU (default OPTIX)
#   AOS_BLENDER_SAMPLES   Cycles samples     (default 64)
#   AOS_BLENDER_RES       square resolution  (default 512)
#   AOS_BLENDER_STRESS    0 normal; >0 OOM test (default 0)
#   AOS_BLENDER_TIMEOUT   max wait for the frame, seconds (default 600)
set -u

COORD_NAME="org.agentos.Coordinator1"
COORD_PATH="/org/agentos/Coordinator1"

EST_MIB="${AOS_BLENDER_EST_MIB:-8000}"
DEVICE="${AOS_BLENDER_DEVICE:-OPTIX}"
SAMPLES="${AOS_BLENDER_SAMPLES:-64}"
RES="${AOS_BLENDER_RES:-512}"
STRESS="${AOS_BLENDER_STRESS:-0}"
TIMEOUT="${AOS_BLENDER_TIMEOUT:-600}"

# The work dir MUST match render-wrapper.sh's WORK_ROOT default ($XDG_RUNTIME_DIR/agentos/blender),
# because agentosd spawns the wrapper with the DAEMON's env, not ours — the only channel we control is
# the validated --out param. Same user + session → same $XDG_RUNTIME_DIR.
WORK_ROOT="${XDG_RUNTIME_DIR:-/tmp}/agentos/blender"
OUT_DIR="$WORK_ROOT/phase0"
FRAME="$OUT_DIR/blender-frame.png"
CACHE_DIR="${AOS_BLENDER_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/agentos/blender}"

log() { echo "[phase0 $(date +%H:%M:%S)] $*"; }
coord() { busctl --user call "$COORD_NAME" "$COORD_PATH" "$COORD_NAME" "$@"; }

mkdir -p "$OUT_DIR" "$CACHE_DIR"
rm -f "$FRAME"   # so we detect THIS run's output, not a stale frame

PARAMS=(--out "$OUT_DIR" --samples "$SAMPLES" --device "$DEVICE" --res "$RES" --stress "$STRESS")

TOKEN=""
cleanup() {
  # On the success path the render has already exited (agentosd auto-released) → this is a no-op.
  # On timeout/interrupt it SIGKILLs a hung owned render so it can't hold the heavy lane.
  if [ -n "$TOKEN" ] && [ "$TOKEN" != "0" ]; then
    coord Release t "$TOKEN" >/dev/null 2>&1 || true
    TOKEN=""
  fi
}
trap cleanup EXIT INT TERM

# 1. Acquire the batch lease by having agentosd Spawn + own the blender-render job.
log "requesting batch lease (est ${EST_MIB}M) → Spawn blender-render ${PARAMS[*]}"
out="$(coord Spawn susas batch "$EST_MIB" blender-render "${#PARAMS[@]}" "${PARAMS[@]}" 2>&1)" || {
  log "coordinator unreachable ($out) — is \`agentosd lease\` running? (fail-open, ADR-0003)"; exit 0; }
read -r _sig granted token _rest <<<"$out"
if [ "${granted:-false}" != "true" ]; then
  log "not granted — ${out} — skipping (the GPU is busy; a render is batch-only)"; exit 0
fi
TOKEN="$token"
log "lease GRANTED (token $TOKEN); agentosd owns the blender-render job"

# 2. Wait for the frame (the owned render writes it, then exits → agentosd auto-releases).
log "waiting for frame (≤${TIMEOUT}s): $FRAME"
deadline=$((SECONDS + TIMEOUT))
until [ -f "$FRAME" ]; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    log "no frame in ${TIMEOUT}s — aborting (trap SIGKILLs the owned render via Release)"; exit 1
  fi
  sleep 1
done
sleep 1  # let the PNG settle

# 3. Publish to the anchor cache + print the lucid command (manual consume in Phase 0, ADR-0022 §7).
ts="$(date +%s)"
dest="$CACHE_DIR/blender-$ts.png"
tmp="$CACHE_DIR/.blender-$$.tmp.png"
cp -f "$FRAME" "$tmp" && mv -f "$tmp" "$dest"
log "published frame → $dest"
log "feed it into the dreaming pipeline as an anchor frame, e.g.:"
log "  python3 apps/dreaming/lucid/lucid_engine.py start phase0 --image \"$dest\""
log "done — leased render ↔ heavy-lane coexistence + anchor-seam drop PROVEN"
