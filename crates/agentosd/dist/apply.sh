#!/usr/bin/env bash
# Install the AgentOS read-only producers as --user services:
#   * nimbus-aurora-agent.service   (P1) → $XDG_RUNTIME_DIR/nimbus-aurora/agent.json   (wallpaper mood)
#   * nimbus-aurora-keyhole.service (ADR-0012) → …/keyhole.json (tray instrument)
# Reversible via restore.sh. Read-only wrt Hermes; the only things they write are those two files.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
BIN_DEST="$HOME/.local/bin/agentosd"
UNITS=(nimbus-aurora-agent.service nimbus-aurora-keyhole.service)
UNIT_DIR="$HOME/.config/systemd/user"
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

echo "building agentosd (release)…"
( cd "$REPO" && cargo build --release -p agentosd )

mkdir -p "$(dirname "$BIN_DEST")"
install -m755 "$REPO/target/release/agentosd" "$BIN_DEST"

mkdir -p "$UNIT_DIR"
for UNIT in "${UNITS[@]}"; do
  install -m644 "$HERE/$UNIT" "$UNIT_DIR/$UNIT"
done

systemctl --user daemon-reload
if systemctl --user enable --now "${UNITS[@]}"; then
  echo "✓ producers installed + started (${UNITS[*]})"
  echo "  → $RUNTIME/nimbus-aurora/agent.json   (wallpaper mood)"
  echo "  → $RUNTIME/nimbus-aurora/keyhole.json (tray instrument)"
else
  echo "! could not enable the user services; start them by hand:"
  echo "    systemctl --user enable --now ${UNITS[*]}"
fi
