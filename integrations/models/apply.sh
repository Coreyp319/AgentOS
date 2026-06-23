#!/usr/bin/env bash
# Install the AgentOS model audit panel as a --user service. Reachable from the status panel (:9123).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT=agentos-models-panel.service
UNIT_DIR="$HOME/.config/systemd/user"
PORT="${AGENTOS_MODELS_PORT:-9124}"

command -v python3 >/dev/null || { echo "✗ python3 not found" >&2; exit 1; }
mkdir -p "$UNIT_DIR"
install -m644 "$HERE/$UNIT" "$UNIT_DIR/$UNIT"
systemctl --user daemon-reload
systemctl --user enable "$UNIT" >/dev/null 2>&1 || true   # restart (not enable --now) so a re-apply applies unit edits
if systemctl --user restart "$UNIT"; then
  echo "✓ model audit panel installed + started → http://127.0.0.1:${PORT}"
  echo "  edit affiliations in integrations/models/registry.json; code + panel both follow it."
  echo "  logs: journalctl --user -u $UNIT -f"
else
  echo "! could not enable; start by hand: systemctl --user enable --now $UNIT" >&2
fi
