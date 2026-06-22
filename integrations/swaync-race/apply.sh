#!/usr/bin/env bash
# Install the swaync notification-bus race fix: make swaync reliably acquire
# org.freedesktop.Notifications before plasmashell, by taking xdg-desktop-portal off swaync's
# cold-start critical path (see swaync.service.d/nimbus-race.conf for the full root cause).
# User-scope, reversible via restore.sh. Takes effect at NEXT login — the name cannot be
# reclaimed mid-session (plasmashell won't release it; swaync requests it without replacement).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"

command -v swaync >/dev/null 2>&1 \
  || echo "! swaync not found on PATH — install SwayNotificationCenter; the units still install" >&2

mkdir -p "$UNIT_DIR/swaync.service.d" "$UNIT_DIR/plasma-plasmashell.service.d"

# Main swaync unit override (Type=dbus + Before=plasmashell). Install only if absent so a
# locally-customized unit is never clobbered; the race fix lives in the drop-in, not here.
if [ ! -e "$UNIT_DIR/swaync.service" ]; then
  install -m644 "$HERE/swaync.service" "$UNIT_DIR/swaync.service"
  echo "✓ installed swaync.service (Type=dbus override)"
else
  echo "• swaync.service already present — left as-is (the fix is in the drop-in)"
fi

# The race config (the fix) — always (re)installed so apply is the source of truth.
install -m644 "$HERE/swaync.service.d/nimbus-race.conf" \
              "$UNIT_DIR/swaync.service.d/nimbus-race.conf"
install -m644 "$HERE/plasma-plasmashell.service.d/after-swaync.conf" \
              "$UNIT_DIR/plasma-plasmashell.service.d/after-swaync.conf"
install -m644 "$HERE/plasma-plasmashell.service.d/gate-on-swaync.conf.disabled" \
              "$UNIT_DIR/plasma-plasmashell.service.d/gate-on-swaync.conf.disabled"

systemctl --user daemon-reload
echo "✓ swaync-race fix installed."
echo "  Effective at your NEXT login. This session: plasmashell keeps serving notifications."
echo "  Verify next login: busctl --user status org.freedesktop.Notifications  (Comm=swaync)"
