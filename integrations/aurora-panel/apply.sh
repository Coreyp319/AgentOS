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

HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
THEMES="$DATA/plasma/desktoptheme"
STATE="$DATA/aurora-theme"          # share aurora-theme's state dir — one look, one revert home
SCHEME="$HERE/../aurora-theme/color-schemes/AuroraDark.colors"

mkdir -p "$STATE"

[ -f "$SCHEME" ] || { echo "! AuroraDark.colors not found ($SCHEME) — apply aurora-theme first. Nothing changed." >&2; exit 1; }

cur="$(kreadconfig6 --file plasmarc --group Theme --key name 2>/dev/null || true)"
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
