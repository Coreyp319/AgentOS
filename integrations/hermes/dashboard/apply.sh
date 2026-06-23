#!/usr/bin/env bash
# Install the Hermes web dashboard as a --user service so the UI is up at boot on
# http://127.0.0.1:9119. Reversible via restore.sh. Independent of hermes-gateway.service.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT=hermes-dashboard.service
UNIT_DIR="$HOME/.config/systemd/user"
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"

if [[ ! -x "$HERMES_PY" ]]; then
  echo "✗ Hermes venv python not found at $HERMES_PY — is ~/.hermes installed?" >&2
  exit 1
fi

mkdir -p "$UNIT_DIR"
install -m644 "$HERE/$UNIT" "$UNIT_DIR/$UNIT"

systemctl --user daemon-reload
systemctl --user enable "$UNIT" >/dev/null 2>&1 || true   # restart (not enable --now) so a re-apply applies unit edits
if systemctl --user restart "$UNIT"; then
  echo "✓ Hermes dashboard installed + started → http://127.0.0.1:9119"
  echo "  logs: journalctl --user -u $UNIT -f"
else
  echo "! could not enable the user service; start it by hand:" >&2
  echo "    systemctl --user enable --now $UNIT" >&2
fi
