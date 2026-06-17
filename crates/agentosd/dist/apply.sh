#!/usr/bin/env bash
# Install the AgentOS read-only producers as --user services:
#   * nimbus-aurora-agent.service   (P1) → $XDG_RUNTIME_DIR/nimbus-aurora/agent.json   (wallpaper mood)
#   * nimbus-aurora-keyhole.service (ADR-0012) → …/keyhole.json (tray instrument)
#   * agentos-telemetry.service     (ADR-0018) → ~/.local/state/agentosd/telemetry.jsonl (coexistence history)
#   * agentos-coexist-report.timer  (ADR-0018) → daily `coexist` snapshot → ~/.local/state/agentosd/coexist-history.txt
# Reversible via restore.sh. Read-only wrt the system; the only things they write are those files.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
BIN_DEST="$HOME/.local/bin/agentosd"
UNITS=(nimbus-aurora-agent.service nimbus-aurora-keyhole.service agentos-telemetry.service)
# Timer-triggered (install both, enable only the .timer).
REPORT_UNITS=(agentos-coexist-report.service agentos-coexist-report.timer)
UNIT_DIR="$HOME/.config/systemd/user"
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/agentosd"

echo "building agentosd (release)…"
( cd "$REPO" && cargo build --release -p agentosd )

mkdir -p "$(dirname "$BIN_DEST")"
install -m755 "$REPO/target/release/agentosd" "$BIN_DEST"

mkdir -p "$UNIT_DIR"
for UNIT in "${UNITS[@]}" "${REPORT_UNITS[@]}"; do
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
if systemctl --user enable --now agentos-coexist-report.timer 2>/dev/null; then
  echo "  → $STATE/coexist-history.txt          (daily coexist go/no-go snapshot)"
else
  echo "  ! enable the report timer by hand: systemctl --user enable --now agentos-coexist-report.timer"
fi
