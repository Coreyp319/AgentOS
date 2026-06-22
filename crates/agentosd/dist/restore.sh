#!/usr/bin/env bash
# Revert apply.sh. Pass --purge to also remove the installed binary + the telemetry history.
set -euo pipefail

UNITS=(agentos-lease.service agentos-queue.service nimbus-aurora-agent.service nimbus-aurora-keyhole.service agentos-telemetry.service \
       nimbus-aurora-scene.service nimbus-aurora-rc.service \
       agentos-coexist-report.timer agentos-coexist-report.service)
UNIT_DIR="$HOME/.config/systemd/user"
BIN_DEST="$HOME/.local/bin/agentosd"
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/agentosd"

for UNIT in "${UNITS[@]}"; do
  systemctl --user disable --now "$UNIT" 2>/dev/null && echo "✓ stopped + disabled $UNIT" \
    || echo "($UNIT was not active)"
  rm -f "$UNIT_DIR/$UNIT"
done
rm -f "$RUNTIME/nimbus-aurora/agent.json" "$RUNTIME/nimbus-aurora/keyhole.json" "$RUNTIME/nimbus-aurora/lease.json" \
      "$RUNTIME/nimbus-aurora/scene-params.json"
# Telemetry history is accumulated user data — kept by default, removed only on --purge.
if [ "${1:-}" = "--purge" ]; then
  rm -f "$BIN_DEST"
  echo "✓ purged $BIN_DEST"
  rm -f "$STATE/telemetry.jsonl" "$STATE/telemetry.jsonl.1" "$STATE/coexist-history.txt"
  echo "✓ purged telemetry + coexist history in $STATE"
fi
systemctl --user daemon-reload 2>/dev/null || true
echo "✓ reverted"
