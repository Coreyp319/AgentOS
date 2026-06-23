#!/usr/bin/env bash
# Aurora control gallery — one window to preview the look (vs hunting through a real app).
#   gallery.sh          # AFTER:  the aurora Union style (6px/12px, solid lavender focus, co-lit accent)
#   gallery.sh before   # BEFORE: stock breeze Union CSS (5px, flat) — same engine, A/B fair
#   gallery.sh both     # launch both side by side
#
# QtQuick Union QML style is per-app only (QT_QUICK_CONTROLS_STYLE=org.kde.union) — NEVER global
# (it breaks Flatpak QML apps). UNION_DISABLE_STYLE_CACHE=1 so freshly-authored CSS shows.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
QML="$(command -v qml6 || command -v qml || command -v qml-qt6 || true)"
[ -n "$QML" ] || { echo "! need the 'qml' runtime — install qt6-declarative (qml6)." >&2; exit 1; }

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"

launch() {  # $1 = before|after
  local style env_name
  if [ "$1" = "before" ]; then
    style=breeze
    # ensure breeze resolves from the user data root (Union looks here first); copy the
    # system breeze once if absent, so the A/B is fair (same engine, stock CSS).
    [ -d "$DATA/union/css/styles/breeze" ] || cp -r /usr/share/union/css/styles/breeze "$DATA/union/css/styles/breeze" 2>/dev/null || true
    echo "BEFORE — stock breeze Union CSS (5px corners, flat, translucent focus)"
  else
    style=aurora
    echo "AFTER  — aurora Union style (6px controls / 12px floating, solid lavender focus, co-lit accent)"
  fi
  UNION_STYLE_NAME="$style" UNION_DISABLE_STYLE_CACHE=1 QT_QUICK_CONTROLS_STYLE=org.kde.union \
    "$QML" "$HERE/gallery.qml" &
}

case "${1:-after}" in
  before) launch before; wait ;;
  after)  launch after;  wait ;;
  both)   launch before; launch after; echo "two windows: arrange side by side."; wait ;;
  *) echo "usage: gallery.sh [before|after|both]" >&2; exit 2 ;;
esac
