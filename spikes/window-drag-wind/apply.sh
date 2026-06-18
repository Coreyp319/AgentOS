#!/usr/bin/env bash
# Install the AgentOS window-drag → wind KWin script REVERSIBLY (spec §6, ADR-0005).
#   1. kpackagetool6 installs the package into ~/.local/share/kwin/scripts/
#   2. kwriteconfig6 enables it in kwinrc [Plugins]  (<Id>Enabled=true)
#   3. qdbus reconfigure hot-reloads KWin's scripting subsystem (no logout)
# Read-only wrt the desktop; the only thing it adds is one disabled-by-default script.
# Revert with restore.sh (drops the [Plugins] key + uninstalls the package).
#
# NOTE: this installs the PRODUCER half only. The CONSUMER (the agentosd `wind` sink that
# serves org.agentos.Wind1 + writes wind.json) is a design proposal, not yet built — until it
# exists the KWin script's callDBus is a harmless no-op (fail-open). See the spec.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PKG="$HERE/kwin-script"
ID="agentos-window-drag-wind"   # must match metadata.json KPlugin.Id

echo "installing KWin script package: $PKG"
# -u upgrades if already present, -i installs fresh; try upgrade, fall back to install.
kpackagetool6 --type KWin/Script -u "$PKG" 2>/dev/null \
  || kpackagetool6 --type KWin/Script -i "$PKG"

echo "enabling in kwinrc [Plugins] ($ID}Enabled=true)"
kwriteconfig6 --file kwinrc --group Plugins --key "${ID}Enabled" true

echo "hot-reloading KWin scripting (no logout)…"
qdbus org.kde.KWin /KWin reconfigure 2>/dev/null \
  || qdbus6 org.kde.KWin /KWin reconfigure 2>/dev/null \
  || echo "  ! could not reach org.kde.KWin — toggle the script in System Settings → KWin Scripts, or re-login"

echo "✓ window-drag → wind producer installed + enabled"
echo "  → watch it: journalctl --user -f | grep agentos-wind   (look for the load line + step prints if you add them)"
echo "  → it emits Gust(...) on org.agentos.Wind1; with no sink running those calls no-op (fail-open)."
