#!/usr/bin/env bash
# Uninstall the AgentOS status panel service + its login opener (reverses apply.sh).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT=agentos-status-panel.service
UNIT_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"

systemctl --user disable --now "$UNIT" 2>/dev/null || true
rm -f "$UNIT_DIR/$UNIT" "$AUTOSTART_DIR/agentos-status-open.desktop"

# Remove the KRunner launchers apply.sh wrote (ADR-0031). Best-effort.
python3 "$HERE/gen_launchers.py" --remove 2>/dev/null || true

# Put back the dashboard-only opener that apply.sh retired, if we backed it up — so apply→restore
# returns the pre-apply login behavior instead of silently dropping it.
OLD_BAK="$AUTOSTART_DIR/hermes-dashboard-open.desktop.agentos-bak"
[ -f "$OLD_BAK" ] && mv -f "$OLD_BAK" "$AUTOSTART_DIR/hermes-dashboard-open.desktop" \
  && echo "✓ restored the prior dashboard login opener"

systemctl --user daemon-reload
echo "✓ AgentOS status panel removed (login opener removed too)"
