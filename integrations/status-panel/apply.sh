#!/usr/bin/env bash
# Install the AgentOS status panel as a --user service AND wire it to open in the
# browser at login (replacing the dashboard-only autostart). Reversible via restore.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT=agentos-status-panel.service
UNIT_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"
PORT="${AGENTOS_STATUS_PORT:-9123}"

command -v python3 >/dev/null || { echo "✗ python3 not found" >&2; exit 1; }

mkdir -p "$UNIT_DIR" "$AUTOSTART_DIR"
install -m644 "$HERE/$UNIT" "$UNIT_DIR/$UNIT"

# The panel is now the login landing page; retire the dashboard-only opener if present.
rm -f "$AUTOSTART_DIR/hermes-dashboard-open.desktop"

cat > "$AUTOSTART_DIR/agentos-status-open.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AgentOS status panel
Comment=Open the AgentOS boot-health panel once it's up after login
# Wait (up to ~30s) for the panel to be serving, then open it in the default browser.
Exec=sh -c 'for i in \$(seq 1 30); do curl -sf http://127.0.0.1:${PORT}/ >/dev/null 2>&1 && break; sleep 1; done; xdg-open http://127.0.0.1:${PORT}'
X-GNOME-Autostart-Delay=6
X-KDE-autostart-after=panel
NoDisplay=false
EOF

systemctl --user daemon-reload
if systemctl --user enable --now "$UNIT"; then
  echo "✓ AgentOS status panel installed + started → http://127.0.0.1:${PORT}"
  echo "  opens automatically at next login; logs: journalctl --user -u $UNIT -f"
else
  echo "! could not enable the user service; start it by hand:" >&2
  echo "    systemctl --user enable --now $UNIT" >&2
fi
