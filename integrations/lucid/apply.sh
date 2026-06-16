#!/usr/bin/env bash
# Install the Lucid web surface as a --user service. It is NOT a login landing page — the
# status panel (:9123) links to it. Reversible via restore.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT=agentos-lucid.service
UNIT_DIR="$HOME/.config/systemd/user"
PORT="${LUCID_WEB_PORT:-8765}"

command -v python3 >/dev/null || { echo "✗ python3 not found" >&2; exit 1; }

mkdir -p "$UNIT_DIR"
install -m644 "$HERE/$UNIT" "$UNIT_DIR/$UNIT"

systemctl --user daemon-reload
if systemctl --user enable --now "$UNIT"; then
  echo "✓ Lucid web surface installed + started → http://127.0.0.1:${PORT}"
  echo "  reachable from the status panel (:9123, Dreaming group)."
  echo "  Note: actually dreaming needs the coordinator (agentosd lease) + ComfyUI + Ollama;"
  echo "  the page is honest about readiness and fails open to the shader otherwise."
  echo "  logs: journalctl --user -u $UNIT -f"
else
  echo "! could not enable the user service; start it by hand:" >&2
  echo "    systemctl --user enable --now $UNIT" >&2
fi
