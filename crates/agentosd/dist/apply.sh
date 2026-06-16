#!/usr/bin/env bash
# Install the AgentOS read-only producers as --user services:
#   * nimbus-aurora-agent.service   (P1) → $XDG_RUNTIME_DIR/nimbus-aurora/agent.json   (wallpaper mood)
#   * nimbus-aurora-keyhole.service (ADR-0012) → …/keyhole.json (tray instrument)
#   * agentos-telemetry.service     (ADR-0018) → ~/.local/state/agentosd/telemetry.jsonl (coexistence history)
# Reversible via restore.sh. Read-only wrt the system; the only things they write are those files.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
BIN_DEST="$HOME/.local/bin/agentosd"
UNITS=(nimbus-aurora-agent.service nimbus-aurora-keyhole.service agentos-telemetry.service)
UNIT_DIR="$HOME/.config/systemd/user"
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/agentosd"

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
  echo "  → $STATE/telemetry.jsonl              (coexistence history)"
else
  echo "! could not enable the user services; start them by hand:"
  echo "    systemctl --user enable --now ${UNITS[*]}"
fi
