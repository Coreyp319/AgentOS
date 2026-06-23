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

# The panel is the login landing page; retire the dashboard-only opener if present — but BACK IT UP
# first (once) so restore.sh can recreate it. Reversible, not a silent one-way migration.
OLD_OPENER="$AUTOSTART_DIR/hermes-dashboard-open.desktop"
if [ -f "$OLD_OPENER" ] && [ ! -e "$OLD_OPENER.agentos-bak" ]; then
  mv "$OLD_OPENER" "$OLD_OPENER.agentos-bak"
else
  rm -f "$OLD_OPENER"
fi

# Ambient-first (ADR-0026, surface-labor contract): the all-clear is silence. The opener
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

# Desktop launch surface (ADR-0031): emit one KRunner `.desktop` launcher per service door, so
# typing a service name (or "agentos") in KRunner opens it. Best-effort — never fail the install.
if python3 "$HERE/gen_launchers.py" --install; then
  :
else
  echo "! could not write KRunner launchers (non-fatal)" >&2
fi

systemctl --user daemon-reload
# `enable` (idempotent symlink) then `restart` — NOT `enable --now`, which is a no-op on an
# already-running service and so would NOT apply a unit-file change (e.g. a new RuntimeDirectory)
# on re-apply. `restart` starts the service if stopped and restarts it if running, so a re-apply
# always picks up unit edits. (This bit ADR-0043: RuntimeDirectory=agentos-adopt didn't take.)
systemctl --user enable "$UNIT" >/dev/null 2>&1 || true
if systemctl --user restart "$UNIT"; then
  echo "✓ AgentOS status panel installed + (re)started → http://127.0.0.1:${PORT}"
  echo "  opens at next login only if something needs attention; else open it from the tray"
  echo "  phone: install the Atrium PWA at http://127.0.0.1:${PORT}/atrium (over your tailnet)"
  echo "  logs: journalctl --user -u $UNIT -f"
else
  echo "! could not start the user service; start it by hand:" >&2
  echo "    systemctl --user enable --now $UNIT" >&2
fi
