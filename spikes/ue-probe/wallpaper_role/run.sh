#!/usr/bin/env bash
# run.sh — ADR-0029 sec.A wallpaper-role test launcher (VISUAL test; run by a human).
#
#   ./run.sh            # plasma org_kde_plasma_surface DESKTOP role (raw wl_surface)
#   ./run.sh --layer    # zwlr_layer_shell BACKGROUND (the PoC-0a comparison)
#
# Both paint a distinct dark-teal full-screen fill (obvious test, not a glitch),
# then tell you to run ./dump_stacking.sh in another shell to read where KWin
# stacked the surface. The two modes share IDENTICAL introspection so the plasma
# desktop role and the wlr BACKGROUND layer can be compared apples-to-apples.
#
# This DOES present a full-screen surface. Do not run it while you need the screen.
# Stop with Ctrl-C, or:  pkill -f '[w]allpaper_role_plasma'   /   pkill -f '[l]ayershell_poc'
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "${WAYLAND_DISPLAY:-}" = "" ]; then
  echo "FATAL: WAYLAND_DISPLAY unset — run inside the live Wayland session." >&2
  exit 1
fi

MODE="${1:-plasma}"
case "$MODE" in
  --layer|layer)
    PRELOAD="/usr/lib/libgtk4-layer-shell.so"
    POC="$HERE/../layershell_poc.py"
    [ -f "$POC" ] || { echo "FATAL: missing $POC" >&2; exit 1; }
    [ -f "$PRELOAD" ] || { echo "FATAL: missing $PRELOAD" >&2; exit 1; }
    echo "[run] MODE = wlr layer-shell BACKGROUND (PoC-0a)"
    echo "[run] presenting; in a 2nd shell run:  $HERE/dump_stacking.sh"
    echo "[run] stop with Ctrl-C or: pkill -f '[l]ayershell_poc'"
    exec env LD_PRELOAD="$PRELOAD" python3 "$POC"
    ;;
  plasma|"")
    BIN="$HERE/wallpaper_role_plasma"
    if [ ! -x "$BIN" ]; then
      echo "[run] building $BIN ..."
      ( cd "$HERE" && ./build.sh )
    fi
    echo "[run] MODE = plasma org_kde_plasma_surface DESKTOP role (raw wl_surface)"
    echo "[run] presenting; in a 2nd shell run:  $HERE/dump_stacking.sh"
    echo "[run] stop with Ctrl-C or: pkill -f '[w]allpaper_role_plasma'"
    exec "$BIN"
    ;;
  *)
    echo "usage: $0 [--layer]" >&2
    exit 2
    ;;
esac
