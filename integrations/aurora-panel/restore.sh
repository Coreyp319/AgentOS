#!/usr/bin/env bash
# Deactivate aurora-panel (reverses apply.sh): repoint the Plasma shell at the theme it had
# before. The aurora clone dir is left inert (the printed one-liner purges it). Fail-open:
# a missing backup falls back to the stock "default" theme. ADR-0042.
set -euo pipefail
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
THEMES="$DATA/plasma/desktoptheme"
STATE="$DATA/aurora-theme"

prev="$(cat "$STATE/prev-plasmatheme" 2>/dev/null || true)"; : "${prev:=default}"
kwriteconfig6 --file plasmarc --group Theme --key name "$prev"
rm -f "$STATE/prev-plasmatheme"

echo "✓ Plasma shell theme restored → $prev (panel/tray back to their original colours)."
echo "  Effective at next login / plasmashell restart. The aurora clone is left inert — to purge:"
echo "    rm -rf \"$THEMES\"/*-aurora"
