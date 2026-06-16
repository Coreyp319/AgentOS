#!/usr/bin/env bash
# Uninstall the Hermes dashboard --user service (reverses apply.sh).
set -euo pipefail

UNIT=hermes-dashboard.service
UNIT_DIR="$HOME/.config/systemd/user"

systemctl --user disable --now "$UNIT" 2>/dev/null || true
rm -f "$UNIT_DIR/$UNIT"
systemctl --user daemon-reload
echo "✓ Hermes dashboard service removed"
