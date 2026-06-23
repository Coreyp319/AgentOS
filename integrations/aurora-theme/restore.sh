#!/usr/bin/env bash
# Deactivate the Aurora theme (reverses apply.sh): restore the prior widget style + colour
# scheme and drop the session env knob. The style + scheme FILES are left installed (inert)
# — the printed one-liner purges them. Fail-open: a missing backup falls back to kvantum.
set -euo pipefail
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
STATE="$DATA/aurora-theme"
ENVD="$HOME/.config/environment.d"

prev_style="$(cat "$STATE/prev-widgetstyle" 2>/dev/null || true)"; : "${prev_style:=kvantum}"
prev_scheme="$(cat "$STATE/prev-colorscheme" 2>/dev/null || true)"

kwriteconfig6 --file kdeglobals --group KDE --key widgetStyle "$prev_style"
if [ -n "$prev_scheme" ]; then
  if command -v plasma-apply-colorscheme >/dev/null 2>&1; then
    plasma-apply-colorscheme "$prev_scheme" >/dev/null 2>&1 || true
  else
    kwriteconfig6 --file kdeglobals --group General --key ColorScheme "$prev_scheme"
  fi
fi
rm -f "$ENVD/union-style.conf"

# Reverse the GTK app re-accent (ADR-0042 OS-cohesion pass). Each guarded; a missing backup just skips.
if [ -f "$STATE/gtk4-gtk.css.preaurora" ]; then
  cp "$STATE/gtk4-gtk.css.preaurora" "$HOME/.config/gtk-4.0/gtk.css" && rm -f "$STATE/gtk4-gtk.css.preaurora"
  echo "✓ GTK4 gtk.css restored (accent back to WhiteSur blue)"
fi
if command -v gsettings >/dev/null 2>&1 && [ -s "$STATE/prev-gtk-accent" ]; then
  prev="$(cat "$STATE/prev-gtk-accent")"; prev="${prev//\'/}"; prev="${prev//[[:space:]]/}"
  [ -n "$prev" ] && gsettings set org.gnome.desktop.interface accent-color "$prev" 2>/dev/null || true
  rm -f "$STATE/prev-gtk-accent"
  echo "✓ libadwaita named accent restored → ${prev:-default}"
fi
for base in gtk-3.0 gtk-4.0; do
  bk="$STATE/$base-settings.ini.preaurora"
  if [ -f "$bk" ]; then
    cp "$bk" "$HOME/.config/$base/settings.ini" && rm -f "$bk"
    echo "✓ $base settings.ini restored (icon-theme back to WhiteSur)"
  fi
done

echo "✓ Aurora deactivated. Restored: widgetStyle → $prev_style${prev_scheme:+, ColorScheme → $prev_scheme}."
echo "  Your desktop is exactly as it was — effective for new apps now, fully at next login."
echo "  (Aurora's style + scheme files are left installed but inactive — to purge:)"
echo "    rm -rf \"$DATA/union/css/styles/aurora\" \"$DATA/color-schemes/AuroraDark.colors\" \"$DATA/color-schemes/AuroraLight.colors\""
