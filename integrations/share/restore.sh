#!/usr/bin/env bash
# Uninstall the AgentOS Share hub service (reverses apply.sh). Leaves ~/.config/agentos/share.env
# (it may hold your Hermes key) and tailscale serve mappings alone — those are removed explicitly:
#   rm ~/.config/agentos/share.env   |   integrations/agentosd-remote.sh down
set -euo pipefail

UNIT=agentos-share.service
UNIT_DIR="$HOME/.config/systemd/user"

systemctl --user disable --now "$UNIT" 2>/dev/null || true
rm -f "$UNIT_DIR/$UNIT"
systemctl --user daemon-reload
echo "✓ Share hub service removed"
echo "  (kept: ~/.config/agentos/share.env if present, and any tailscale serve :8770 mapping)"
