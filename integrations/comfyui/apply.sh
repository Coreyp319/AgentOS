#!/usr/bin/env bash
# Install the ComfyUI --user unit, but leave it DISABLED on purpose.
#
# ADR-0015 / the lease design (ADR-0006/0010): the agentosd VRAM coordinator OWNS ComfyUI's
# lifecycle. The dreaming path (lucid_web / create_from_image, via lucid_linear.lease_spawn)
# asks agentosd to *Spawn* a coordinator-owned ComfyUI under a batch lease, so a preempt can
# SIGKILL it and reclaim VRAM. An always-on comfyui.service breaks that two ways:
#   1. it holds :8188, so start-comfyui.sh's port-race guard refuses the coordinator-owned
#      Spawn (exit 3) and every dream fails OPEN — requests never reach ComfyUI; and
#   2. it squats VRAM idle, tightening the admission knife-edge.
# So this unit is installed for MANUAL standalone ComfyUI work only (UI iteration with NO dream
# running) and is NOT enabled or started. Start it by hand when you want a standalone server.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT=comfyui.service
UNIT_DIR="$HOME/.config/systemd/user"
COMFY="${COMFY_ROOT:-$HOME/ComfyUI}"

if [[ ! -x "$COMFY/.venv/bin/python" ]]; then
  echo "✗ ComfyUI venv python not found at $COMFY/.venv/bin/python" >&2
  echo "  Set COMFY_ROOT or install ComfyUI first; aborting." >&2
  exit 1
fi

mkdir -p "$UNIT_DIR"
install -m644 "$HERE/$UNIT" "$UNIT_DIR/$UNIT"
systemctl --user daemon-reload

# Self-heal: a prior apply (commit c97a611 enabled+started it) leaves an always-on instance that
# breaks the coordinator-owned dream spawn. Undo it idempotently — disabled is the correct state.
systemctl --user disable --now "$UNIT" 2>/dev/null || true

echo "✓ ComfyUI unit installed but DISABLED — the coordinator owns ComfyUI under the lease (ADR-0015)."
echo "  Dreams spawn ComfyUI on demand; there is nothing to start at boot."
echo "  For MANUAL standalone ComfyUI (no dream running): systemctl --user start comfyui.service"
