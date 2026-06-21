#!/usr/bin/env bash
# Remove the AgentOS window-drag → wind KWin script (reverses apply.sh). Fully reversible:
# drops the kwinrc [Plugins] enable key, uninstalls the package, hot-reloads KWin, and clears
# the runtime wind.json. Leaves the desktop exactly as before. Does NOT touch the sink (the
# lease daemon / core-substrate) — this component is the producer half only. User-scope.
set -euo pipefail

ID="agentos-window-drag-wind"
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

if ! command -v kwriteconfig6 >/dev/null 2>&1; then
  echo "• window-drag-wind: kwriteconfig6 not found — nothing to remove"
  exit 0
fi

# --delete removes the key entirely (no `false` residue left in kwinrc).
kwriteconfig6 --file kwinrc --group Plugins --key "${ID}Enabled" --delete 2>/dev/null \
  || kwriteconfig6 --file kwinrc --group Plugins --key "${ID}Enabled" false
echo "disabled in kwinrc [Plugins]"

if command -v kpackagetool6 >/dev/null 2>&1; then
  kpackagetool6 --type KWin/Script -r "$ID" >/dev/null 2>&1 && echo "uninstalled KWin script package" \
    || echo "($ID was not installed)"
fi

qdbus org.kde.KWin /KWin reconfigure >/dev/null 2>&1 \
  || qdbus6 org.kde.KWin /KWin reconfigure >/dev/null 2>&1 \
  || echo "  ! could not reach org.kde.KWin — re-login to fully clear"

# wind.json is runtime-only; drop any leftover so a stale gust can't linger past uninstall.
rm -f "$RUNTIME/nimbus-aurora/wind.json"
echo "✓ reverted — producer uninstalled, kwinrc key removed, wind.json cleared"
