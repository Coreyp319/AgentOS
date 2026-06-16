#!/usr/bin/env bash
# Uninstall the model audit panel (reverses apply.sh).
set -euo pipefail
UNIT=agentos-models-panel.service
systemctl --user disable --now "$UNIT" 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/$UNIT"
systemctl --user daemon-reload
echo "✓ model audit panel removed"
