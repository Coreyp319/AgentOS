#!/usr/bin/env bash
# Remove the swaync notification-bus race fix (reverses apply.sh): drop the race config so
# notification ownership reverts to stock Plasma (plasmashell). Fail-open. The swaync.service
# unit itself is left in place (it predates this integration); remove it by hand to fully revert.
set -euo pipefail

UNIT_DIR="$HOME/.config/systemd/user"

rm -f "$UNIT_DIR/swaync.service.d/nimbus-race.conf" \
      "$UNIT_DIR/plasma-plasmashell.service.d/after-swaync.conf" \
      "$UNIT_DIR/plasma-plasmashell.service.d/gate-on-swaync.conf.disabled" \
      "$UNIT_DIR/plasma-plasmashell.service.d/gate-on-swaync.conf"   # in case it was activated
rmdir "$UNIT_DIR/swaync.service.d" 2>/dev/null || true
rmdir "$UNIT_DIR/plasma-plasmashell.service.d" 2>/dev/null || true

systemctl --user daemon-reload
echo "✓ swaync-race drop-ins removed; plasmashell reclaims notifications at next login."
echo "  swaync.service left in place — 'rm ~/.config/systemd/user/swaync.service' to fully revert."
