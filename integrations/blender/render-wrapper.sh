#!/usr/bin/env bash
# render-wrapper.sh — the daemon-owned program for the `blender-render` Spawn profile (ADR-0022 §3/§7).
#
# agentosd resolves the profile name to THIS absolute path itself (lease.rs PROFILES, ADR-0013 A2) —
# a caller never passes a binary. The Spawn `params` are appended as LITERAL argv (execv, no shell),
# and this wrapper accepts ONLY validated typed scalars. It then `exec`s Blender so the owned PID is
# blender → SIGKILL-on-preempt frees the CUDA context.
#
# Hardening (ADR-0022):
#   - the render script (render.py) is FIXED and repo-owned, NEVER a param;
#   - --factory-startup --disable-autoexec → a malicious .blend can't auto-run embedded Python;
#   - the output dir (and optional input .blend) are validated to live under a work root (no ../symlink
#     escape), so the agent can't write/read outside its sandbox;
#   - Cycles' own VRAM is kept conservative (small tiles + texture limit, in render.py) so a heavy
#     scene fails its frame rather than wedging the driver;
#   - non-zero exit on any failure → a failed render is distinguishable from a completed one at the
#     lease layer (the supervisor logs the status).
#
# Param contract (all optional except --out):
#   --out <dir>      output directory (MUST be under WORK_ROOT); the frame is <dir>/blender-frame.png
#   --samples <n>    Cycles samples            (1..4096,  default 64)
#   --device <D>     OPTIX | CUDA | CPU         (default OPTIX)
#   --res <n>        square resolution px       (16..16384, default 512)
#   --stress <n>     0 normal; >0 = OOM acceptance test (0..11, default 0)
#   --blend <path>   optional input .blend      (MUST be under WORK_ROOT; default = factory scene)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RENDER_PY="$HERE/render.py"
# The sandbox work root the agent is confined to. In Phase 2 this is the bind-mounted work dir of the
# §6 namespace; in Phase 0/1 it defaults under XDG_RUNTIME_DIR. Override with AOS_BLENDER_WORK_ROOT.
WORK_ROOT="${AOS_BLENDER_WORK_ROOT:-${XDG_RUNTIME_DIR:-/tmp}/agentos/blender}"
BLENDER_BIN="${AOS_BLENDER_BIN:-blender}"

die() { echo "[render-wrapper] ERROR: $*" >&2; exit 2; }

# --- parse the typed-scalar param contract ---
OUT=""; SAMPLES=64; DEVICE="OPTIX"; RES=512; STRESS=0; BLEND=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --out)     OUT="${2:-}"; shift 2 ;;
    --samples) SAMPLES="${2:-}"; shift 2 ;;
    --device)  DEVICE="${2:-}"; shift 2 ;;
    --res)     RES="${2:-}"; shift 2 ;;
    --stress)  STRESS="${2:-}"; shift 2 ;;
    --blend)   BLEND="${2:-}"; shift 2 ;;
    *) die "unknown/forbidden param: $1 (only validated scalars are accepted)" ;;
  esac
done

# --- validate every scalar (fail-closed) ---
is_uint() { [[ "$1" =~ ^[0-9]+$ ]]; }
is_uint "$SAMPLES" && [ "$SAMPLES" -ge 1 ] && [ "$SAMPLES" -le 4096 ] || die "--samples out of range"
is_uint "$RES" && [ "$RES" -ge 16 ] && [ "$RES" -le 16384 ] || die "--res out of range"
is_uint "$STRESS" && [ "$STRESS" -ge 0 ] && [ "$STRESS" -le 11 ] || die "--stress out of range"
case "$DEVICE" in OPTIX|CUDA|CPU) ;; *) die "--device must be OPTIX|CUDA|CPU" ;; esac
[ -n "$OUT" ] || die "--out <dir> is required"

# Path containment: resolve and confirm the dir is INSIDE the work root (blocks ../ + symlink escape).
mkdir -p "$WORK_ROOT"
WORK_ROOT_REAL="$(realpath -m "$WORK_ROOT")"
OUT_REAL="$(realpath -m "$OUT")"
case "$OUT_REAL/" in "$WORK_ROOT_REAL"/*) ;; *) die "--out escapes the work root ($WORK_ROOT_REAL)" ;; esac
mkdir -p "$OUT_REAL"

BLEND_ARG=()
if [ -n "$BLEND" ]; then
  BLEND_REAL="$(realpath -m "$BLEND")"
  case "$BLEND_REAL" in "$WORK_ROOT_REAL"/*) ;; *) die "--blend escapes the work root" ;; esac
  [ -f "$BLEND_REAL" ] || die "--blend not found: $BLEND_REAL"
  BLEND_ARG=("$BLEND_REAL")
fi

[ -f "$RENDER_PY" ] || die "fixed render script missing: $RENDER_PY"
command -v "$BLENDER_BIN" >/dev/null 2>&1 || die "blender not found (set AOS_BLENDER_BIN or install Blender)"

export AOS_BLENDER_OUT="$OUT_REAL/blender-frame.png"
export AOS_BLENDER_SAMPLES="$SAMPLES"
export AOS_BLENDER_DEVICE="$DEVICE"
export AOS_BLENDER_RES="$RES"
export AOS_BLENDER_STRESS="$STRESS"

echo "[render-wrapper] exec blender (device=$DEVICE res=$RES spp=$SAMPLES stress=$STRESS) → $AOS_BLENDER_OUT" >&2
# exec → the owned PID becomes blender; --factory-startup --disable-autoexec are the safety flags.
exec "$BLENDER_BIN" --factory-startup --disable-autoexec -b "${BLEND_ARG[@]}" --python "$RENDER_PY"
