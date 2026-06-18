#!/usr/bin/env bash
# Revert apply.sh — fully reversible (spec §6, ADR-0005). Removes the [Plugins] enable key,
# uninstalls the package, and hot-reloads KWin. Leaves the desktop exactly as before.
set -euo pipefail

ID="agentos-window-drag-wind"

echo "disabling in kwinrc [Plugins]…"
# deleteEntry removes the key entirely (cleaner than setting false — no residue in kwinrc).
kwriteconfig6 --file kwinrc --group Plugins --key "${ID}Enabled" --delete 2>/dev/null \
  || kwriteconfig6 --file kwinrc --group Plugins --key "${ID}Enabled" false

echo "uninstalling KWin script package…"
kpackagetool6 --type KWin/Script -r "$ID" 2>/dev/null \
  || echo "  ($ID was not installed)"

echo "hot-reloading KWin scripting…"
qdbus org.kde.KWin /KWin reconfigure 2>/dev/null \
  || qdbus6 org.kde.KWin /KWin reconfigure 2>/dev/null \
  || echo "  ! could not reach org.kde.KWin — re-login to fully clear"

# wind.json is runtime-only ($XDG_RUNTIME_DIR); remove any leftover so a stale file can't
# linger past uninstall. (The sink, when built, also writes a final neutral frame on exit.)
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
rm -f "$RUNTIME/nimbus-aurora/wind.json"

echo "✓ reverted — producer uninstalled, kwinrc key removed, wind.json cleared"
