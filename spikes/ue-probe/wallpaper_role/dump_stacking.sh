#!/usr/bin/env bash
# dump_stacking.sh — ADR-0029 §A self-diagnostic wrapper.
#
# Loads kwin_dump_stacking.js into the running KWin, runs it, and tails the
# resulting stacking-order dump out of the journal. Non-destructive: it only
# READS the window stack. Run it AFTER a PoC client is presenting, so its
# full-screen surface shows up in the list.
#
#   ./dump_stacking.sh
#
# Read the [N] lines: index 0 is the BOTTOM (drawn first / behind everything).
#
# NOTE: KWin caches a loaded script by FILE PATH and will serve a stale compile
# if you reload the same path. We sidestep that by copying the JS to a unique
# temp path per run, so every run recompiles the current source.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/kwin_dump_stacking.js"
PLUGIN="agentos_dump_$$_$RANDOM"
TMP="${XDG_RUNTIME_DIR:-/tmp}/${PLUGIN}.js"

if ! command -v qdbus6 >/dev/null 2>&1; then
  echo "FATAL: qdbus6 not found" >&2; exit 1
fi
[ -f "$SRC" ] || { echo "FATAL: missing $SRC" >&2; exit 1; }
cp "$SRC" "$TMP"

echo "[dump] loading KWin script: $TMP (plugin=$PLUGIN)"
CURSOR="$(journalctl --user -t kwin_wayland -t kwin_x11 -n0 --show-cursor 2>/dev/null \
          | sed -n 's/^-- cursor: //p' | tail -1 || true)"

ID="$(qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript "$TMP" "$PLUGIN" 2>/dev/null || echo -1)"
echo "[dump] loadScript returned id=$ID"
qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.start >/dev/null 2>&1 || true

# Let KWin run the script + flush to the journal (background sleep; foreground is blocked).
( sleep 1 ) & wait $!

echo "[dump] ---- KWin stacking dump (from journal) ----"
# NB: journal lines are prefixed with a timestamp, so anchor the index match with
# '\[[0-9]+\]' ANYWHERE in the line, not '^\['.
if [ -n "${CURSOR:-}" ]; then
  journalctl --user -t kwin_wayland -t kwin_x11 --after-cursor "$CURSOR" --no-pager 2>/dev/null \
    | grep -E 'STACKING ORDER|\[[0-9]+\]|<unreadable|END STACKING|ERROR' || \
    echo "[dump] (no lines matched — open a 2nd shell with: journalctl --user -t kwin_wayland -f , then re-run)"
else
  journalctl --user -t kwin_wayland -t kwin_x11 -n 300 --no-pager 2>/dev/null \
    | grep -E 'STACKING ORDER|\[[0-9]+\]|<unreadable|END STACKING|ERROR' | tail -90 || \
    echo "[dump] (no lines matched)"
fi

qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript "$PLUGIN" >/dev/null 2>&1 || true
rm -f "$TMP"
echo "[dump] done. index 0 = BOTTOM/behind."
echo "[dump] BASELINE on this box: index 0 is plasmashell 3440x1440 (the desktop-icon view);"
echo "[dump] a later plasmashell ~72px-tall line is the panel/dock."
echo "[dump] PASS (plasma desktop role): the PoC full-screen surface sits AT/BELOW index 0"
echo "[dump]       (below the plasmashell desktop view) — i.e. behind icons + panel + windows."
echo "[dump] FAIL (the PoC-0a layer-shell result): PoC surface sits ABOVE the plasmashell desktop view."
