#!/usr/bin/env bash
# Aurora panel — re-accent the Plasma SHELL (panel, system tray, plasmoids, applets) to the
# Aurora violet so the always-on desktop surface matches the violet windows + GTK apps instead
# of the stock WhiteSur blue. ADR-0042 OS-cohesion pass (2026-06-23). User-scope, reversible.
#
# Why a separate component: the Plasma desktop theme ships its OWN `colors` file (blue) which
# OVERRIDES the system colour scheme for the shell — so aurora-theme's AuroraDark scheme reaches
# Qt apps but NOT the panel. We CLONE the active theme to "<name>-aurora", swap that clone's
# `colors` for the Aurora Dark scheme (single source of truth — the violet lives in ONE place,
# aurora-theme/color-schemes/AuroraDark.colors), and repoint plasmarc at the clone. The original
# theme is never touched, so revert = repoint back. Off by default (opt-in, and unrenderable in
# the authoring harness — verify on screen at relogin).
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

HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
THEMES="$DATA/plasma/desktoptheme"
STATE="$DATA/aurora-theme"          # share aurora-theme's state dir — one look, one revert home
SCHEME="$HERE/../aurora-theme/color-schemes/AuroraDark.colors"

mkdir -p "$STATE"

[ -f "$SCHEME" ] || { echo "! AuroraDark.colors not found ($SCHEME) — apply aurora-theme first. Nothing changed." >&2; exit 1; }

cur="$(kread_ini plasmarc Theme name)"
: "${cur:=default}"
case "$cur" in
  *-aurora) clone="$cur" ;;                        # already on an aurora clone — just refresh its colours
  *)        clone="${cur}-aurora"
            [ -s "$STATE/prev-plasmatheme" ] || printf '%s\n' "$cur" > "$STATE/prev-plasmatheme" ;;
esac

src="$THEMES/$cur"; dst="$THEMES/$clone"
if [ "$cur" != "$clone" ]; then
  if [ ! -d "$src" ]; then
    echo "! active Plasma theme dir not found ($src) — can't clone. Nothing changed." >&2
    echo "  (A system theme lives in /usr/share/plasma/desktoptheme; copy it into $THEMES first.)" >&2
    exit 2
  fi
  rm -rf "$dst"
  cp -r "$src" "$dst"
  # Give the clone a distinct metadata Id (Plasma keys off the dir name, but keep metadata honest).
  [ -f "$dst/metadata.json" ] && sed -i 's/\("Id"[[:space:]]*:[[:space:]]*\)"[^"]*"/\1"'"$clone"'"/' "$dst/metadata.json" 2>/dev/null || true
fi

# Swap the clone's colours for the Aurora violet scheme (single source of truth = AuroraDark.colors).
install -m644 "$SCHEME" "$dst/colors"
echo "✓ cloned '$cur' → '$clone'; set its panel/tray colours to Aurora Dark (violet)"

# Repoint the shell at the aurora clone.
kwriteconfig6 --file plasmarc --group Theme --key name "$clone"
echo "✓ Plasma shell theme → $clone"
echo "  • The panel/tray re-accent takes effect at NEXT login (or restart plasmashell)."
echo "  • Revert: $HERE/restore.sh"
