#!/usr/bin/env bash
# Revert apply.sh. Pass --purge to also remove the installed binary.
set -euo pipefail

UNIT="nimbus-aurora-agent.service"
UNIT_DEST="$HOME/.config/systemd/user/$UNIT"
BIN_DEST="$HOME/.local/bin/agentosd"
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

systemctl --user disable --now "$UNIT" 2>/dev/null && echo "✓ stopped + disabled $UNIT" \
  || echo "(service was not active)"
rm -f "$RUNTIME/nimbus-aurora/agent.json"
rm -f "$UNIT_DEST"
if [ "${1:-}" = "--purge" ]; then
  rm -f "$BIN_DEST"
  echo "✓ purged $BIN_DEST"
fi
systemctl --user daemon-reload 2>/dev/null || true
echo "✓ reverted"
