#!/usr/bin/env bash
# Aurora light/dark switch. The Union 'aurora' style is mode-agnostic (one CSS); light vs
# dark lives entirely in the active KColorScheme, so switching modes = swapping the scheme
# between Aurora Light and Aurora Dark. Reversible, no relog needed for the colour swap.
#   aurora-mode.sh            # toggle
#   aurora-mode.sh light|dark # set explicitly
set -euo pipefail

mode="${1:-toggle}"
cur="$(kreadconfig6 --file kdeglobals --group General --key ColorScheme 2>/dev/null || true)"
if [ "$mode" = "toggle" ]; then
  [ "$cur" = "AuroraLight" ] && mode=dark || mode=light
fi
case "$mode" in
  light) scheme=AuroraLight ;;
  dark)  scheme=AuroraDark  ;;
  *) echo "usage: aurora-mode.sh [light|dark|toggle]" >&2; exit 2 ;;
esac

if command -v plasma-apply-colorscheme >/dev/null 2>&1; then
  plasma-apply-colorscheme "$scheme" >/dev/null 2>&1 \
    || kwriteconfig6 --file kdeglobals --group General --key ColorScheme "$scheme"
else
  kwriteconfig6 --file kdeglobals --group General --key ColorScheme "$scheme"
fi
echo "Aurora → $mode ($scheme)"
