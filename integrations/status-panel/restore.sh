#!/usr/bin/env bash
# Uninstall the AgentOS status panel service + its login opener (reverses apply.sh).
set -euo pipefail

UNIT=agentos-status-panel.service
UNIT_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"

systemctl --user disable --now "$UNIT" 2>/dev/null || true
rm -f "$UNIT_DIR/$UNIT" "$AUTOSTART_DIR/agentos-status-open.desktop"
systemctl --user daemon-reload
echo "✓ AgentOS status panel removed (login opener removed too)"
