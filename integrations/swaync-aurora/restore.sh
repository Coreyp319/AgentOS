#!/usr/bin/env bash
# AgentOS — remove the Aurora swaync style (reverses apply.sh).
# Restores ~/.config/swaync/style.css.preaurora if present (the original
# macOS-mimic / user customisation); otherwise removes our installed style.css
# so swaync falls back to its packaged default. Fail-open, idempotent — never
# errors if already reverted. NEVER launches swaync. ADR-0005.
set -euo pipefail

CFG_DIR="$HOME/.config/swaync"
LIVE="$CFG_DIR/style.css"
BACKUP="$CFG_DIR/style.css.preaurora"

if [ -e "$BACKUP" ]; then
  mv -f "$BACKUP" "$LIVE"
  echo "✓ restored prior style.css from $BACKUP"
elif [ -e "$LIVE" ]; then
  rm -f "$LIVE"
  echo "✓ removed Aurora style.css — swaync falls back to its packaged default."
else
  echo "• nothing to restore (no style.css and no backup) — already reverted."
fi

# Reload ONLY if swaync is already running — never launch it.
if command -v swaync-client >/dev/null 2>&1 && pgrep -x swaync >/dev/null 2>&1; then
  swaync-client --reload-css    >/dev/null 2>&1 || true
  swaync-client --reload-config >/dev/null 2>&1 || true
  echo "✓ swaync is running — reloaded CSS live."
else
  echo "• swaync not running — NOT launching it."
  echo "  (effective on next swaync start / reload)"
fi
