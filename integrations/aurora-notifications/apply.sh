#!/usr/bin/env bash
# Aurora notifications — give the NATIVE Plasma notification popups the Aurora
# "instrument glass" look now that swaync is retired and plasmashell owns
# org.freedesktop.Notifications. ADR-0042 (OS-cohesion). User-scope, reversible.
#
# WHAT ACTUALLY CONTROLS THE POPUP LOOK (verified on this box, Plasma 6.7.1):
#   The popup is custom Plasma QML — a PlasmaQuick::Dialog rendered by plasmashell,
#   NOT QtWidgets and NOT generic QtQuick.Controls. So Union CSS does NOT reach it.
#   Its appearance comes from the active PLASMA DESKTOP THEME:
#     • chrome (card body, corner radius, hairline, drop-shadow) = the theme's
#       dialogs/background.svgz  (shared by every floating Plasma dialog: the
#       notification toast, tray popups, OSDs, applet popups).
#     • accent/text/buttons inside the popup = the theme's `colors` file, which —
#       because the desktop theme ships its own `colors` — OVERRIDES the system
#       colour scheme for shell surfaces. The violet accent is delivered by the
#       sibling `aurora-panel` component (which sets the clone's `colors` to
#       Aurora Dark). This component owns the CHROME: it swaps the card body from
#       neutral dark #1a1a1a to the Aurora instrument navy #161a28 — the exact
#       toast colour the retired swaync-aurora style used (rgba(22,26,40,...)).
#
# COMPOSES WITH aurora-panel: both repoint the shell at one "<theme>-aurora"
# clone. This script reuses that clone if it exists, else creates one. Run
# aurora-panel too for the violet accent inside the popup; this alone gives the
# navy glass + the radius/lift. Either order is fine.
#
# SCOPE NOTE (honest): dialogs/background.svgz is shared by ALL Plasma floating
# dialogs, so tray/applet popups get the same navy glass — which is the intended
# Aurora instrument register, not a side effect. Fully reverted by restore.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
THEMES="$DATA/plasma/desktoptheme"
STATE="$DATA/aurora-theme"          # share aurora-theme/-panel's revert home — one look, one revert
SRC_SVGZ="$HERE/dialog-background.svgz"

[ -f "$SRC_SVGZ" ] || { echo "! $SRC_SVGZ missing — nothing to install. Aborting." >&2; exit 1; }
mkdir -p "$STATE"

# 1. Resolve the target theme: reuse the active aurora clone if the shell is
#    already on one (aurora-panel ran), else clone the active theme now.
cur="$(kreadconfig6 --file plasmarc --group Theme --key name 2>/dev/null || true)"
: "${cur:=default}"
case "$cur" in
  *-aurora) clone="$cur" ;;                         # already on an aurora clone — just retune its dialog chrome
  *)        clone="${cur}-aurora"
            [ -s "$STATE/prev-plasmatheme" ] || printf '%s\n' "$cur" > "$STATE/prev-plasmatheme" ;;
esac

src="$THEMES/$cur"; dst="$THEMES/$clone"
if [ "$cur" != "$clone" ]; then
  if [ ! -d "$src" ]; then
    # The active theme may live system-wide; copy it into the user themes first.
    if [ -d "/usr/share/plasma/desktoptheme/$cur" ]; then
      src="/usr/share/plasma/desktoptheme/$cur"
    else
      echo "! active Plasma theme dir not found ($THEMES/$cur or /usr/share/...) — can't clone. Nothing changed." >&2
      exit 2
    fi
  fi
  if [ ! -d "$dst" ]; then
    cp -r "$src" "$dst"
    [ -f "$dst/metadata.json" ] && sed -i 's/\("Id"[[:space:]]*:[[:space:]]*\)"[^"]*"/\1"'"$clone"'"/' "$dst/metadata.json" 2>/dev/null || true
    echo "✓ cloned '$cur' → '$clone'"
  else
    echo "• aurora clone '$clone' already present — reusing it"
  fi
fi

# 2. Back up the clone's original dialog chrome ONCE, then install the Aurora navy one.
mkdir -p "$dst/dialogs"
if [ -f "$dst/dialogs/background.svgz" ] && [ ! -f "$STATE/dialog-background.svgz.preaurora" ]; then
  # Only back up if it isn't already OURS (idempotent re-apply must not clobber the original).
  if ! cmp -s "$SRC_SVGZ" "$dst/dialogs/background.svgz"; then
    cp -p "$dst/dialogs/background.svgz" "$STATE/dialog-background.svgz.preaurora"
    echo "✓ backed up the clone's dialog chrome → $STATE/dialog-background.svgz.preaurora"
  fi
fi
install -m644 "$SRC_SVGZ" "$dst/dialogs/background.svgz"
echo "✓ installed Aurora instrument-navy dialog chrome (#161a28 card, ~12px radius, soft lift)"

# 3. Point the shell at the clone (no-op if aurora-panel already did).
if [ "$(kreadconfig6 --file plasmarc --group Theme --key name 2>/dev/null)" != "$clone" ]; then
  kwriteconfig6 --file plasmarc --group Theme --key name "$clone"
  echo "✓ Plasma shell theme → $clone"
fi

echo
echo "✓ Aurora notifications applied."
echo "  • Effective at next login / plasmashell restart (plasmarc + theme SVG cache are read at start)."
echo "  • Pair with aurora-panel for the violet accent INSIDE the popup (action buttons / focus)."
echo "  • Test a popup any time:   notify-send -a AgentOS 'Aurora test' 'styled notification body'"
echo "  • Revert:                  $HERE/restore.sh"
