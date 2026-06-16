#!/usr/bin/env bash
# Uninstall the Lucid web service (reverses apply.sh).
set -euo pipefail

UNIT=agentos-lucid.service
UNIT_DIR="$HOME/.config/systemd/user"

systemctl --user disable --now "$UNIT" 2>/dev/null || true
rm -f "$UNIT_DIR/$UNIT"
systemctl --user daemon-reload
echo "✓ Lucid web service removed"
