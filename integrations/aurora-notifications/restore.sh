#!/usr/bin/env bash
# Deactivate aurora-notifications (reverses apply.sh): restore the aurora clone's
# original dialog chrome. If THIS component created the clone / repointed the shell
# (i.e. aurora-panel is NOT also installed), also repoint the shell back to the
# original theme. Fail-open. ADR-0042.
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
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
THEMES="$DATA/plasma/desktoptheme"
STATE="$DATA/aurora-theme"

cur="$(kread_ini plasmarc Theme name)"
: "${cur:=default}"

# 1. Restore the dialog chrome inside whatever aurora clone is active.
case "$cur" in
  *-aurora) dst="$THEMES/$cur" ;;
  *)        dst="" ;;   # shell not on an aurora clone — nothing of ours to restore in-theme
esac
if [ -n "$dst" ] && [ -d "$dst/dialogs" ]; then
  if [ -f "$STATE/dialog-background.svgz.preaurora" ]; then
    install -m644 "$STATE/dialog-background.svgz.preaurora" "$dst/dialogs/background.svgz"
    rm -f "$STATE/dialog-background.svgz.preaurora"
    echo "✓ restored the clone's original dialog chrome (popups back to the stock card colour)."
  else
    # No backup => we never overwrote an original (clone was made by us from scratch);
    # leaving the navy SVG is harmless, but if we own the clone fully it goes away in step 2.
    echo "• no dialog-chrome backup found — leaving the clone's dialog SVG as-is."
  fi
fi

# Drop the applied-marker so the adopt engine (ADR-0043) reads this component as available again.
rm -f "$STATE/aurora-notifications.on"

# 2. If aurora-panel is NOT managing the shell theme (no prev-plasmatheme set by it would
#    still be here), repoint the shell back only if WE created the clone and aurora-panel
#    isn't holding it. We detect aurora-panel's ownership by the presence of the panel's
#    own colors swap: the clone's `colors` differs from the original. To stay safe and
#    non-destructive we DO NOT auto-repoint when a clone exists (aurora-panel may want it);
#    instead we print the exact revert so the user (or aurora-panel/restore.sh) finishes it.
prev="$(cat "$STATE/prev-plasmatheme" 2>/dev/null || true)"
if [ -n "$prev" ]; then
  echo "  If you are NOT using aurora-panel, finish reverting the shell theme with:"
  echo "    kwriteconfig6 --file plasmarc --group Theme --key name \"$prev\""
  echo "  (aurora-panel/restore.sh does this for you if that component is installed.)"
fi
echo "  Effective at next login / plasmashell restart."
