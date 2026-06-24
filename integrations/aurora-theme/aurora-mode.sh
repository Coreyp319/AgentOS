#!/usr/bin/env bash
# Aurora light/dark switch. The Union 'aurora' style is mode-agnostic (one CSS); light vs
# dark lives entirely in the active KColorScheme, so switching modes = swapping the scheme
# between Aurora Light and Aurora Dark. Reversible, no relog needed for the colour swap.
#   aurora-mode.sh            # toggle
#   aurora-mode.sh light|dark # set explicitly
set -euo pipefail

# kread_ini: read a KDE config value via awk instead of kreadconfig6. kreadconfig6 is a Qt GUI
# binary that write-locks its own ~/.config/kreadconfig6rc even to READ, and pops a blocking
# "kreadconfig6rc not writable" modal when that probe fails (cold boot / a read-only-home unit).
# awk has no toolkit, so it never stalls or shows a dialog. Searches the XDG cascade (config-home
# wins), like kreadconfig6. Args: file group key. Writes still use kwriteconfig6.
kread_ini() {
  local b IFS=:
  for b in "${XDG_CONFIG_HOME:-$HOME/.config}" ${XDG_CONFIG_DIRS:-/etc/xdg}; do
    if [ -r "$b/$1" ]; then
      awk -v g="[$2]" -v k="$3" '
        $0==g {f=1; next} /^\[/ {f=0}
        f { i=index($0,"="); if (i>0 && substr($0,1,i-1)==k) { print substr($0,i+1); exit } }' "$b/$1"
    fi
  done | head -n1
}

mode="${1:-toggle}"
cur="$(kread_ini kdeglobals General ColorScheme)"
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
