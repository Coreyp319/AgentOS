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

# The panel is the login landing page; retire the dashboard-only opener if present.
rm -f "$AUTOSTART_DIR/hermes-dashboard-open.desktop"

# Ambient-first (ADR-0017, surface-labor contract): the all-clear is silence. The opener
# waits for the panel to serve, gives the stack a settle window, then opens the browser
# ONLY if something needs attention (summary.attention > 0). A clean boot opens nothing —
# the keyhole tray carries the calm. Plain grep (no jq); json.dumps emits `"attention": N`.
cat > "$AUTOSTART_DIR/agentos-status-open.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AgentOS status panel
Comment=Open the AgentOS boot-health panel at login only if a service needs attention
Exec=sh -c 'p="http://127.0.0.1:${PORT}"; for i in \$(seq 1 30); do curl -sf "\$p/" >/dev/null 2>&1 && break; sleep 1; done; for i in \$(seq 1 12); do curl -sf "\$p/status.json" 2>/dev/null | grep -qE "\"attention\": *[1-9]" && { xdg-open "\$p"; break; }; sleep 2; done'
X-GNOME-Autostart-Delay=6
X-KDE-autostart-after=panel
NoDisplay=false
EOF

systemctl --user daemon-reload
if systemctl --user enable --now "$UNIT"; then
  echo "✓ AgentOS status panel installed + started → http://127.0.0.1:${PORT}"
  echo "  opens at next login only if something needs attention; else open it from the tray"
  echo "  logs: journalctl --user -u $UNIT -f"
else
  echo "! could not enable the user service; start it by hand:" >&2
  echo "    systemctl --user enable --now $UNIT" >&2
fi
