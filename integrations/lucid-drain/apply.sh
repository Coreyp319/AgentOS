#!/usr/bin/env bash
# Install the Lucid drain timer (ADR-0019 §5): a monotonic poll that re-runs ONE deferred
# "Create Video from Image" request per fire under a BestEffort lease. The .service/.timer live in
# spikes/dreaming/lucid/dist/ but nothing installed them — so deferred requests never drained.
# Reversible via restore.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/../../spikes/dreaming/lucid/dist" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
DRAINER="$(cd "$HERE/../../spikes/dreaming/lucid" && pwd)/lucid_drain.py"

command -v python3 >/dev/null || { echo "✗ python3 not found" >&2; exit 1; }
[ -f "$SRC/lucid-drain.service" ] || { echo "✗ missing $SRC/lucid-drain.service" >&2; exit 1; }
[ -f "$SRC/lucid-drain.timer" ]   || { echo "✗ missing $SRC/lucid-drain.timer" >&2; exit 1; }
[ -f "$DRAINER" ] || { echo "✗ drainer body missing: $DRAINER" >&2; exit 1; }

mkdir -p "$UNIT_DIR"
install -m644 "$SRC/lucid-drain.service" "$UNIT_DIR/lucid-drain.service"
install -m644 "$SRC/lucid-drain.timer"   "$UNIT_DIR/lucid-drain.timer"

systemctl --user daemon-reload
systemctl --user enable lucid-drain.timer >/dev/null 2>&1 || true   # restart so a re-apply applies unit edits
if systemctl --user restart lucid-drain.timer; then
  echo "✓ Lucid drain timer installed + armed (polls the durable request queue)"
  echo "  logs: journalctl --user -u lucid-drain.service -f"
else
  echo "! could not enable the timer; arm it by hand:  systemctl --user enable --now lucid-drain.timer" >&2
fi
