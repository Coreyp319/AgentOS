#!/usr/bin/env bash
# Install the AgentOS agent-state feed (P1) as a --user service.
# Reversible via restore.sh. Read-only wrt Hermes; the only thing it writes is
# $XDG_RUNTIME_DIR/nimbus-aurora/agent.json.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
BIN_DEST="$HOME/.local/bin/agentosd"
UNIT="nimbus-aurora-agent.service"
UNIT_DEST="$HOME/.config/systemd/user/$UNIT"

echo "building agentosd (release)…"
( cd "$REPO" && cargo build --release -p agentosd )

mkdir -p "$(dirname "$BIN_DEST")"
install -m755 "$REPO/target/release/agentosd" "$BIN_DEST"

mkdir -p "$(dirname "$UNIT_DEST")"
install -m644 "$HERE/$UNIT" "$UNIT_DEST"

systemctl --user daemon-reload
if systemctl --user enable --now "$UNIT"; then
  echo "✓ agent feed installed + started ($UNIT)"
  echo "  → ${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/nimbus-aurora/agent.json"
else
  echo "! could not enable the user service; start it by hand:"
  echo "    systemctl --user enable --now $UNIT"
fi
