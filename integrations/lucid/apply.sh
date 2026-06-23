#!/usr/bin/env bash
# Install the Lucid web surface as a --user service. It is NOT a login landing page — the
# status panel (:9123) links to it. Reversible via restore.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT=agentos-lucid.service
UNIT_DIR="$HOME/.config/systemd/user"
PORT="${LUCID_WEB_PORT:-8765}"

command -v python3 >/dev/null || { echo "✗ python3 not found" >&2; exit 1; }

# The React bundle (web/dist) is gitignored, so a fresh clone has none — lucid_web.py then serves a
# degraded inline page instead of the full UI. Best-effort build it; print the command if npm is absent.
WEB="$(cd "$HERE/../.." && pwd)/spikes/dreaming/lucid/web"
if [ ! -d "$WEB/dist" ]; then
  if command -v npm >/dev/null 2>&1 && [ -f "$WEB/package.json" ]; then
    echo "building the Lucid web bundle (web/dist)…"
    ( cd "$WEB" && { npm ci --silent || npm install --silent; } && npm run build ) \
      || echo "! web build failed — Lucid serves the degraded inline page until: (cd $WEB && npm ci && npm run build)"
  else
    echo "! Lucid web bundle (web/dist) missing and npm unavailable — Lucid serves the degraded inline page."
    echo "  build the full UI with: (cd $WEB && npm ci && npm run build)"
  fi
fi

mkdir -p "$UNIT_DIR"
install -m644 "$HERE/$UNIT" "$UNIT_DIR/$UNIT"

systemctl --user daemon-reload
systemctl --user enable "$UNIT" >/dev/null 2>&1 || true   # restart (not enable --now) so a re-apply applies unit edits
if systemctl --user restart "$UNIT"; then
  echo "✓ Lucid web surface installed + started → http://127.0.0.1:${PORT}"
  echo "  reachable from the status panel (:9123, Dreaming group)."
  echo "  Note: actually dreaming needs the coordinator (agentosd lease) + ComfyUI + Ollama;"
  echo "  the page is honest about readiness and fails open to the shader otherwise."
  echo "  logs: journalctl --user -u $UNIT -f"
else
  echo "! could not enable the user service; start it by hand:" >&2
  echo "    systemctl --user enable --now $UNIT" >&2
fi
