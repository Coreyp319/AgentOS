#!/usr/bin/env bash
# Uninstall the Lucid drain timer (reverses apply.sh). Deferred requests stay spooled (we don't
# touch the queue) — they simply stop draining until re-applied.
set -euo pipefail

UNIT_DIR="$HOME/.config/systemd/user"

systemctl --user disable --now lucid-drain.timer 2>/dev/null || true
systemctl --user stop lucid-drain.service 2>/dev/null || true
rm -f "$UNIT_DIR/lucid-drain.timer" "$UNIT_DIR/lucid-drain.service"
systemctl --user daemon-reload
echo "✓ Lucid drain timer removed (spooled requests left intact)"
