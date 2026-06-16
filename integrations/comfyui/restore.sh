#!/usr/bin/env bash
# Uninstall the ComfyUI --user service (reverses apply.sh). Leaves ComfyUI itself untouched.
set -euo pipefail

UNIT=comfyui.service
UNIT_DIR="$HOME/.config/systemd/user"

systemctl --user disable --now "$UNIT" 2>/dev/null || true
rm -f "$UNIT_DIR/$UNIT"
systemctl --user daemon-reload
echo "✓ ComfyUI service removed (ComfyUI install left in place)"
