#!/usr/bin/env bash
# Revert apply.sh. Pass --purge to also remove the installed binary.
set -euo pipefail

UNITS=(nimbus-aurora-agent.service nimbus-aurora-keyhole.service)
UNIT_DIR="$HOME/.config/systemd/user"
BIN_DEST="$HOME/.local/bin/agentosd"
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

for UNIT in "${UNITS[@]}"; do
  systemctl --user disable --now "$UNIT" 2>/dev/null && echo "✓ stopped + disabled $UNIT" \
    || echo "($UNIT was not active)"
  rm -f "$UNIT_DIR/$UNIT"
done
rm -f "$RUNTIME/nimbus-aurora/agent.json" "$RUNTIME/nimbus-aurora/keyhole.json"
if [ "${1:-}" = "--purge" ]; then
  rm -f "$BIN_DEST"
  echo "✓ purged $BIN_DEST"
fi
systemctl --user daemon-reload 2>/dev/null || true
echo "✓ reverted"
