#!/usr/bin/env bash
# Install the xdg-desktop-portal cold-boot start-timeout fix (see timeout.conf for the full
# root cause). User-scope, reversible via restore.sh. Restores a sane 90s start timeout for the
# portal only, so an early-activated portal at cold boot waits for Plasma core instead of being
# killed at CachyOS's global 15s budget — which otherwise leaves the portal dead (breaks
# screencast + plasmashell portal registration + stalls GTK/Qt clients querying Settings at boot).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DROP="$HOME/.config/systemd/user/xdg-desktop-portal.service.d"

mkdir -p "$DROP"
install -m644 "$HERE/timeout.conf" "$DROP/timeout.conf"

systemctl --user daemon-reload
echo "✓ portal-timeout installed (TimeoutStartSec=90 for xdg-desktop-portal)."
echo "  Takes effect at your NEXT cold boot. Verify (now): "
echo "    systemctl --user show xdg-desktop-portal.service -p TimeoutStartUSec   # = 1min 30s"
