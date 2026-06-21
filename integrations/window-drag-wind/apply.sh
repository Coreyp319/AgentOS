#!/usr/bin/env bash
# Install the AgentOS window-drag → wind KWin script — the PRODUCER half of the ambient
# window-drag-to-wind loop (Design 0023 / ADR-0023). It reads window-MOVE geometry deltas and
# emits a drag vector (Gust) to the agentosd wind sink over the session bus; the sink (shipped
# in core-substrate as crate::wind, mounted on the lease daemon's org.agentos.Coordinator1
# connection) runs the deterministic spring and writes wind.json for the reactive wallpaper.
#
# Geometry deltas only — never window titles, identity, or content. Reversible via restore.sh.
# A KWin reconfigure HOT-RELOADS the script (no logout, no shell restart). User-scope.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PKG="$HERE/package"
ID="agentos-window-drag-wind"   # must match package/metadata.json KPlugin.Id
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# Desktop-tier component: on a host without KWin tooling, skip cleanly rather than fail the run.
if ! command -v kpackagetool6 >/dev/null 2>&1 || ! command -v kwriteconfig6 >/dev/null 2>&1; then
  echo "• window-drag-wind: kpackagetool6/kwriteconfig6 not found (not a Plasma 6 / KWin desktop?) — skipping"
  exit 0
fi
[ -f "$PKG/metadata.json" ] || { echo "✗ window-drag-wind: package not found at $PKG" >&2; exit 1; }

# Idempotent: -u upgrades an existing copy, else -i installs fresh.
kpackagetool6 --type KWin/Script -u "$PKG" >/dev/null 2>&1 \
  || kpackagetool6 --type KWin/Script -i "$PKG" >/dev/null
echo "✓ KWin script installed ($ID)"

kwriteconfig6 --file kwinrc --group Plugins --key "${ID}Enabled" true
echo "  enabled in kwinrc [Plugins]"

# Hot-reload KWin's scripting subsystem — this is what makes the script live WITHOUT a logout
# (a real advantage over plasmoids, which need a plasmashell restart). reconfigure is reversible.
if qdbus org.kde.KWin /KWin reconfigure >/dev/null 2>&1 \
   || qdbus6 org.kde.KWin /KWin reconfigure >/dev/null 2>&1; then
  echo "  KWin hot-reloaded — producer is live now"
else
  echo "  ! could not reach org.kde.KWin — toggle it in System Settings → KWin Scripts, or re-login"
fi

# Gentle sink check — without the sink the producer's Gust calls are harmless no-ops (fail-open).
if command -v busctl >/dev/null 2>&1 && busctl --user list 2>/dev/null | grep -q org.agentos.Coordinator1; then
  echo "  sink live: org.agentos.Wind1 is being served (lease daemon up) → wind.json will update on drag"
else
  echo "  ! wind sink not on the bus yet — install/run 'core-substrate' (the lease daemon serves"
  echo "    org.agentos.Wind1); until then drags emit Gust calls that simply no-op (fail-open)"
fi

echo "  → drag a normal window; the reactive wallpaper gusts in that direction. Watch the producer:"
echo "    journalctl --user -f | grep agentos-wind"
