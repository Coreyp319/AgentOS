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

# backup_once: atomically capture the ORIGINAL of a file exactly once (temp-in-same-dir + rename,
# so a crash mid-backup can't leave a torn backup that a later restore copies over the good file).
# A second apply finds the backup present and leaves the original capture intact.
backup_once() {  # backup_once <src-file> <backup-path>
  [ -f "$1" ] || return 0
  [ -s "$2" ] && return 0
  cp "$1" "$2.tmp.$$" && mv -f "$2.tmp.$$" "$2"
}

# fail_gate: a validation gate failed — back the just-copied (never-activated) style out, then exit.
# Uniform across all three gates, so a contrast failure cleans up exactly like a load/lint failure.
fail_gate() { rm -rf "$UNION/styles/aurora"; exit "$1"; }

# 1. Install the aurora style (clean replace) + the REQUIRED defaults/ mirror. Copy the
#    system defaults rather than vendoring a stale copy: Union's CSS dialect can shift
#    between releases, so the defaults must match the INSTALLED engine.
if [ -d "$UNION/styles/aurora" ] && ! diff -rq "$HERE/css/styles/aurora" "$UNION/styles/aurora" >/dev/null 2>&1; then
  echo "! the installed aurora style differs from the repo — replacing it with the repo copy."
  echo "  (If you were iterating via css-tx.py, those edits remain checkpointed under $UNION/.tx/ —"
  echo "   recover with the skill's css-tx.py revert before re-running apply.sh.)"
fi
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
    fail_gate 3
  fi
  echo "✓ aurora style loads clean (union-ruleinspector gate passed)"
fi

# 2a. Structural CSS lint (code disposes): replaces the old single bad-token grep with lint-css.py,
#     which asserts (a) every focus outline is SOLID (--focus-ring-color, never a translucent
#     set-alpha token — WCAG 1.4.11) AND (b) FOCUS COVERAGE — every interactive widget that styles
#     hover/pressed also has a keyboard focus ring (WCAG 2.4.7). The coverage check is the guard
#     that keeps the focus ring from silently dropping off a widget again.
if command -v python3 >/dev/null 2>&1; then
  if ! python3 "$HERE/tools/lint-css.py" "$UNION/styles/aurora" >/dev/null 2>&1; then
    echo "✗ aurora CSS failed the structural lint (solid-outline / focus-coverage) — NOT activating:" >&2
    python3 "$HERE/tools/lint-css.py" "$UNION/styles/aurora" 2>&1 | grep -E '✗' | head -6 >&2 || true
    fail_gate 5
  fi
  echo "✓ aurora CSS passes the solid-outline + focus-coverage lint"
fi

# 2b. WCAG gate (code disposes): the schemes must clear AA before we install them — the
#     accessibility verifier disposes, it is not decorative. Now also covers the COMPUTED mix()
#     colours (menu shortcut text, placeholder) the CSS synthesises, not just raw scheme keys.
if command -v python3 >/dev/null 2>&1; then
  if ! python3 "$HERE/tools/check-contrast.py" \
        "$HERE/color-schemes/AuroraDark.colors" "$HERE/color-schemes/AuroraLight.colors" >/dev/null 2>&1; then
    echo "✗ a colour scheme fails WCAG AA — NOT installing. Detail:" >&2
    python3 "$HERE/tools/check-contrast.py" "$HERE/color-schemes/AuroraDark.colors" "$HERE/color-schemes/AuroraLight.colors" 2>&1 | grep -i fail >&2 || true
    fail_gate 4
  fi
  echo "✓ Aurora Dark + Light clear WCAG AA (contrast gate passed)"
fi

# 3. Install both colour schemes.
install -m644 "$HERE/color-schemes/AuroraDark.colors"  "$SCHEMES/AuroraDark.colors"
install -m644 "$HERE/color-schemes/AuroraLight.colors" "$SCHEMES/AuroraLight.colors"
echo "✓ installed Aurora Dark + Aurora Light colour schemes"

# 4. Session env knob — load the aurora CSS (Flatpak-safe; NOT the QT_QUICK_CONTROLS_STYLE
#    var, which leaks into Flatpak sandboxes and breaks their QML apps). Back up any pre-existing
#    value ONCE first: a user may have set their own UNION_STYLE_NAME per the union-css-theming
#    skill, and restore.sh must put it back rather than silently delete it.
backup_once "$ENVD/union-style.conf" "$STATE/union-style.conf.preaurora"
printf 'UNION_STYLE_NAME=aurora\n' > "$ENVD/union-style.conf.tmp.$$" && mv -f "$ENVD/union-style.conf.tmp.$$" "$ENVD/union-style.conf"

# 5. Back up the prior widget style + colour scheme ONCE, then activate (reversible).
#    Never record "Union" as the thing to restore to (that would be a no-op revert);
#    fall back to kvantum, this box's pre-Union widget style.
cur_style="$(kread_ini kdeglobals KDE widgetStyle)"
[ "$cur_style" = "Union" ] && cur_style=kvantum
[ -s "$STATE/prev-widgetstyle" ] || printf '%s\n' "${cur_style:-kvantum}" > "$STATE/prev-widgetstyle"
cur_scheme="$(kread_ini kdeglobals General ColorScheme)"
# self-exclude an Aurora scheme (so a re-apply can't record Aurora as "previous"); braces make the
# precedence explicit (the bare `[ A ] || [ B ] && C` form only worked by happy associativity).
{ [ "$cur_scheme" = "AuroraDark" ] || [ "$cur_scheme" = "AuroraLight" ]; } && cur_scheme=""
# Never let an EMPTY capture mean "leave the Aurora palette stamped on revert": fall back to the
# Breeze default so restore always has a real scheme to re-apply (mirrors widgetStyle→kvantum).
[ -n "$cur_scheme" ] || cur_scheme="BreezeDark"
[ -s "$STATE/prev-colorscheme" ] || printf '%s\n' "$cur_scheme" > "$STATE/prev-colorscheme"

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

# 7a. GTK4 accent: swap the WhiteSur blue accent defines → Aurora violet (case-insensitive, so a
#     lowercase #0860f2 is matched too).
if [ -f "$GTK4D/gtk.css" ] && grep -qi "$WHITESUR_BLUE" "$GTK4D/gtk.css" 2>/dev/null; then
  backup_once "$GTK4D/gtk.css" "$STATE/gtk4-gtk.css.preaurora"
  sed -i "s/$WHITESUR_BLUE/$AURORA_VIOLET/gI" "$GTK4D/gtk.css"
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
  # match WhiteSur and its variants (WhiteSur-dark, WhiteSur-light, …), not only the bare name.
  if [ -f "$ini" ] && grep -qE '^gtk-icon-theme-name=WhiteSur(-[A-Za-z]+)?$' "$ini" 2>/dev/null; then
    base="$(basename "$(dirname "$ini")")"
    backup_once "$ini" "$STATE/$base-settings.ini.preaurora"
    sed -i -E 's/^(gtk-icon-theme-name=)WhiteSur(-[A-Za-z]+)?$/\1Nimbus-dark-refined/' "$ini"
    echo "✓ ${base} icon-theme → Nimbus-dark-refined (was WhiteSur)"
  fi
done

echo "✓ Aurora theme applied (widgetStyle=Union, scheme=Aurora Dark, GTK4+icons re-accented)."
echo "  • Full session restyle (the aurora CSS) takes effect at NEXT login."
echo "  • Switch modes any time:  $HERE/aurora-mode.sh light | dark | toggle"
echo "  • Deactivate (revert):    $HERE/restore.sh  (restores the prior style + scheme; files stay inert)"
