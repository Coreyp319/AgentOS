#!/usr/bin/env bash
# Aurora theme — install the AgentOS 'aurora' Union CSS style + the Aurora Light/Dark
# colour schemes, activate Union as the widget style, and wire the light/dark switch.
# User-scope, reversible via restore.sh. The Union *style* is mode-agnostic (one CSS
# source); light/dark lives in the colour scheme, because Union 6.7 exposes no "is-dark"
# selector to CSS. ADR-0042. Union is a Plasma 6.7 tech preview → this component is
# opt-in (off by default in components.conf).
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
UNION="$DATA/union/css"
SCHEMES="$DATA/color-schemes"
STATE="$DATA/aurora-theme"
ENVD="$HOME/.config/environment.d"

# 0. Union must be installed (the style needs the engine + the defaults/ base it builds on).
if [ ! -d /usr/share/union/css/defaults ]; then
  echo "! Union not installed (no /usr/share/union/css/defaults) — install 'union' first." >&2
  echo "  Plasma 6.7 ships it. Aborting; nothing changed." >&2
  exit 1
fi

mkdir -p "$UNION/styles" "$SCHEMES" "$STATE" "$ENVD"

# 1. Install the aurora style (clean replace) + the REQUIRED defaults/ mirror. Copy the
#    system defaults rather than vendoring a stale copy: Union's CSS dialect can shift
#    between releases, so the defaults must match the INSTALLED engine.
rm -rf "$UNION/styles/aurora"
cp -r "$HERE/css/styles/aurora" "$UNION/styles/aurora"
[ -d "$UNION/defaults" ] || cp -r /usr/share/union/css/defaults "$UNION/defaults"

# 2. THE GATE (code disposes): the installed style must load under union-ruleinspector,
#    else back it out and abort — never activate a style that throws (ADR-0042/0034).
if command -v union-ruleinspector >/dev/null 2>&1; then
  probe="$(UNION_STYLE_NAME=aurora UNION_DISABLE_STYLE_CACHE=1 \
           union-ruleinspector --style org.kde.union --type Button 2>&1 || true)"
  if printf '%s' "$probe" | grep -qiE 'cxxbridge|panicked|IO Error|terminate called|No such file'; then
    echo "✗ aurora style failed the union-ruleinspector load gate — NOT activating:" >&2
    printf '%s\n' "$probe" | grep -iE 'cxxbridge|panicked|IO Error|terminate|No such' | head -3 >&2
    rm -rf "$UNION/styles/aurora"
    exit 3
  fi
  echo "✓ aurora style loads clean (union-ruleinspector gate passed)"
fi

# 2a. Focus-binding lint (code disposes): a focus outline must use the SOLID --focus-ring-color
#     (via --focus-outline / --button-focus), NEVER the translucent --focus-color hover fill, which
#     composites <3:1 and fails WCAG 2.4.13. Catches the binding trap the design council found in a
#     green-WCAG-gate-over-bad-binding state, so it cannot recur.
bad="$(grep -rn 'outline:[^;]*--focus-color' "$UNION/styles/aurora"/*.css 2>/dev/null || true)"
if [ -n "$bad" ]; then
  echo "✗ aurora has a translucent focus outline (must be the solid --focus-ring-color) — NOT activating:" >&2
  printf '%s\n' "$bad" >&2
  rm -rf "$UNION/styles/aurora"
  exit 5
fi

# 2b. WCAG gate (code disposes): the schemes must clear AA before we install them — the
#     accessibility verifier disposes, it is not decorative.
if command -v python3 >/dev/null 2>&1; then
  if ! python3 "$HERE/tools/check-contrast.py" \
        "$HERE/color-schemes/AuroraDark.colors" "$HERE/color-schemes/AuroraLight.colors" >/dev/null 2>&1; then
    echo "✗ a colour scheme fails WCAG AA — NOT installing. Detail:" >&2
    python3 "$HERE/tools/check-contrast.py" "$HERE/color-schemes/AuroraDark.colors" "$HERE/color-schemes/AuroraLight.colors" 2>&1 | grep -i fail >&2 || true
    exit 4
  fi
  echo "✓ Aurora Dark + Light clear WCAG AA (contrast gate passed)"
fi

# 3. Install both colour schemes.
install -m644 "$HERE/color-schemes/AuroraDark.colors"  "$SCHEMES/AuroraDark.colors"
install -m644 "$HERE/color-schemes/AuroraLight.colors" "$SCHEMES/AuroraLight.colors"
echo "✓ installed Aurora Dark + Aurora Light colour schemes"

# 4. Session env knob — load the aurora CSS (Flatpak-safe; NOT the QT_QUICK_CONTROLS_STYLE
#    var, which leaks into Flatpak sandboxes and breaks their QML apps).
printf 'UNION_STYLE_NAME=aurora\n' > "$ENVD/union-style.conf"

# 5. Back up the prior widget style + colour scheme ONCE, then activate (reversible).
#    Never record "Union" as the thing to restore to (that would be a no-op revert);
#    fall back to kvantum, this box's pre-Union widget style.
cur_style="$(kread_ini kdeglobals KDE widgetStyle)"
[ "$cur_style" = "Union" ] && cur_style=kvantum
[ -s "$STATE/prev-widgetstyle" ] || printf '%s\n' "${cur_style:-kvantum}" > "$STATE/prev-widgetstyle"
cur_scheme="$(kread_ini kdeglobals General ColorScheme)"
[ "$cur_scheme" = "AuroraDark" ] || [ "$cur_scheme" = "AuroraLight" ] && cur_scheme=""
[ -s "$STATE/prev-colorscheme" ] || [ -z "$cur_scheme" ] || printf '%s\n' "$cur_scheme" > "$STATE/prev-colorscheme"

kwriteconfig6 --file kdeglobals --group KDE --key widgetStyle Union

# 6. Default to Aurora Dark (palette-identical to a prior CoreyLavender, so no visual
#    surprise — only the brand name changes). Switch any time with aurora-mode.sh.
if command -v plasma-apply-colorscheme >/dev/null 2>&1; then
  plasma-apply-colorscheme AuroraDark >/dev/null 2>&1 || true
else
  kwriteconfig6 --file kdeglobals --group General --key ColorScheme AuroraDark
fi

# 7. GTK app re-accent (ADR-0042 OS-cohesion pass, 2026-06-23) — pull GTK4/libadwaita apps
#    onto the SAME violet as the Qt chrome so "one accent" holds across toolkits. (GTK3 already
#    carries the violet via ~/.config/gtk-3.0/colors.css.) Every edit is backed up to $STATE once
#    and reversed by restore.sh; each step is guarded + fail-soft (a missing file just skips).
AURORA_VIOLET="#765CC4"     # = AuroraDark Selection bg 118,92,196; white-on-violet clears AA (4.92:1)
WHITESUR_BLUE="#0860F2"     # stock WhiteSur GTK4 accent (high-chroma blue) being replaced
GTK4D="$HOME/.config/gtk-4.0"; GTK3D="$HOME/.config/gtk-3.0"

# 7a. GTK4 accent: swap the WhiteSur blue accent defines → Aurora violet.
if [ -f "$GTK4D/gtk.css" ] && grep -q "$WHITESUR_BLUE" "$GTK4D/gtk.css" 2>/dev/null; then
  [ -f "$STATE/gtk4-gtk.css.preaurora" ] || cp "$GTK4D/gtk.css" "$STATE/gtk4-gtk.css.preaurora"
  sed -i "s/$WHITESUR_BLUE/$AURORA_VIOLET/g" "$GTK4D/gtk.css"
  echo "✓ GTK4 accent → Aurora violet (was WhiteSur blue $WHITESUR_BLUE)"
fi

# 7b. libadwaita named accent: apps that ignore the gtk.css override honor only a NAMED accent
#     (GTK accepts blue/teal/green/yellow/orange/red/pink/purple/slate) — 'purple' is closest to violet.
if command -v gsettings >/dev/null 2>&1; then
  [ -s "$STATE/prev-gtk-accent" ] || gsettings get org.gnome.desktop.interface accent-color > "$STATE/prev-gtk-accent" 2>/dev/null || true
  gsettings set org.gnome.desktop.interface accent-color 'purple' 2>/dev/null \
    && echo "✓ libadwaita named accent → purple (closest named value to Aurora violet)" || true
fi

# 7c. Icon-theme unify: GTK still pointed at WhiteSur icons while Qt/GTK4 use Nimbus — align all
#     toolkits to one icon family.
for ini in "$GTK3D/settings.ini" "$GTK4D/settings.ini"; do
  if [ -f "$ini" ] && grep -q '^gtk-icon-theme-name=WhiteSur$' "$ini" 2>/dev/null; then
    base="$(basename "$(dirname "$ini")")"
    [ -f "$STATE/$base-settings.ini.preaurora" ] || cp "$ini" "$STATE/$base-settings.ini.preaurora"
    sed -i 's/^gtk-icon-theme-name=WhiteSur$/gtk-icon-theme-name=Nimbus-dark-refined/' "$ini"
    echo "✓ ${base} icon-theme → Nimbus-dark-refined (was WhiteSur)"
  fi
done

echo "✓ Aurora theme applied (widgetStyle=Union, scheme=Aurora Dark, GTK4+icons re-accented)."
echo "  • Full session restyle (the aurora CSS) takes effect at NEXT login."
echo "  • Switch modes any time:  $HERE/aurora-mode.sh light | dark | toggle"
echo "  • Deactivate (revert):    $HERE/restore.sh  (restores the prior style + scheme; files stay inert)"
