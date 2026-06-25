#!/usr/bin/env bash
# Deactivate the Aurora theme (reverses apply.sh): restore the prior widget style + colour
# scheme and drop the session env knob. The style + scheme FILES are left installed (inert)
# — the printed one-liner purges them. Fail-open: a missing backup falls back to kvantum.
set -euo pipefail
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
STATE="$DATA/aurora-theme"
ENVD="$HOME/.config/environment.d"

# kread_ini: read a KDE config value via awk, never kreadconfig6 (which write-locks its own rc even
# to read and pops a blocking modal under a read-only-home / cold-boot unit). Args: file group key.
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

prev_style="$(cat "$STATE/prev-widgetstyle" 2>/dev/null || true)"; : "${prev_style:=kvantum}"
prev_scheme="$(cat "$STATE/prev-colorscheme" 2>/dev/null || true)"

# Best-effort per item: a revert must never abort half-way (kwriteconfig6 can fail under a
# read-only-home / cold-boot unit — see the kread_ini note). Each step records its own failure;
# we report the set at the end instead of letting `set -e` strand the remaining reverts.
failed=()

# Widget style — the one write with a known failure mode; don't let it gate the rest.
kwriteconfig6 --file kdeglobals --group KDE --key widgetStyle "$prev_style" \
  || failed+=("widget style (still Union — set Application Style → $prev_style manually)")

# Colour scheme. Legacy installs may have recorded an EMPTY prev-scheme; if so and the active
# scheme is still Aurora, fall back to Breeze so the Aurora palette can't survive the revert.
if [ -z "$prev_scheme" ]; then
  cur="$(kread_ini kdeglobals General ColorScheme)"
  case "$cur" in AuroraDark|AuroraLight) prev_scheme="BreezeDark" ;; esac
fi
if [ -n "$prev_scheme" ]; then
  if command -v plasma-apply-colorscheme >/dev/null 2>&1; then
    plasma-apply-colorscheme "$prev_scheme" >/dev/null 2>&1 \
      || kwriteconfig6 --file kdeglobals --group General --key ColorScheme "$prev_scheme" \
      || failed+=("colour scheme (set Colors → $prev_scheme manually)")
  else
    kwriteconfig6 --file kdeglobals --group General --key ColorScheme "$prev_scheme" \
      || failed+=("colour scheme (set Colors → $prev_scheme manually)")
  fi
fi

# Env knob: restore a pre-existing UNION_STYLE_NAME if apply backed one up, else remove our file.
if [ -f "$STATE/union-style.conf.preaurora" ]; then
  cp "$STATE/union-style.conf.preaurora" "$ENVD/union-style.conf" \
    && rm -f "$STATE/union-style.conf.preaurora" \
    && echo "✓ UNION_STYLE_NAME restored to its prior value" \
    || failed+=("UNION_STYLE_NAME env knob")
else
  rm -f "$ENVD/union-style.conf"
fi

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

if [ "${#failed[@]}" -eq 0 ]; then
  echo "✓ Aurora deactivated. Restored: widgetStyle → $prev_style${prev_scheme:+, ColorScheme → $prev_scheme}."
  echo "  Your desktop is exactly as it was — effective for new apps now, fully at next login."
else
  echo "⚠ Aurora partially deactivated — these items could NOT be reverted automatically:"
  for f in "${failed[@]}"; do echo "    • $f"; done
  echo "  Everything else was restored; re-run restore.sh once the above is resolved."
fi
echo "  (Aurora's style + scheme files are left installed but inactive — to purge:)"
echo "    rm -rf \"$DATA/union/css/styles/aurora\" \"$DATA/color-schemes/AuroraDark.colors\" \"$DATA/color-schemes/AuroraLight.colors\""
