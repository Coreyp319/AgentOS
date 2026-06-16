#!/usr/bin/env bash
# Install ComfyUI as a --user service so the dreaming backend is up at boot on 127.0.0.1:8188.
# Reversible via restore.sh. Does not touch ComfyUI itself — only adds the systemd unit.
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
if systemctl --user enable --now "$UNIT"; then
  echo "✓ ComfyUI installed + started → http://127.0.0.1:8188 (UI + HTTP API)"
  echo "  logs: journalctl --user -u $UNIT -f"
else
  echo "! could not enable the user service; start it by hand:" >&2
  echo "    systemctl --user enable --now $UNIT" >&2
fi
