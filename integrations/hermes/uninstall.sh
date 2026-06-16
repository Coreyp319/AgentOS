#!/usr/bin/env bash
# Revert install.sh: disable + remove the needs-you-signal Hermes plugin.
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
NAME="needs-you-signal"

command -v hermes >/dev/null 2>&1 && hermes plugins disable "$NAME" 2>/dev/null \
  && echo "✓ disabled via hermes CLI" || true
rm -rf "$HERMES_HOME/plugins/$NAME"
rm -f "$HERMES_HOME/needs_you.json"
echo "✓ removed plugin dir + needs_you.json"

if systemctl --user is-active --quiet hermes-gateway.service 2>/dev/null; then
  systemctl --user restart hermes-gateway.service && echo "✓ hermes-gateway restarted"
fi
echo "note: if you enabled it by editing config.yaml, also remove '$NAME' from plugins.enabled there"
