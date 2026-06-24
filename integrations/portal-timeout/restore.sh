#!/usr/bin/env bash
# Reverse apply.sh: drop the portal start-timeout override → xdg-desktop-portal reverts to the
# user manager's default start timeout (CachyOS's 15s). Fail-open.
set -euo pipefail

DROP="$HOME/.config/systemd/user/xdg-desktop-portal.service.d"

rm -f "$DROP/timeout.conf"
rmdir "$DROP" 2>/dev/null || true

systemctl --user daemon-reload
echo "✓ portal-timeout removed; xdg-desktop-portal reverts to the default start timeout."
