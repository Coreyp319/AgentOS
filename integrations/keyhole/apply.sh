#!/usr/bin/env bash
# Install (or upgrade) the AgentOS Keyhole tray plasmoid — the read-only ADR-0012 instrument
# that renders the keyhole.json feed written by the agentosd `keyhole` producer (shipped in the
# core-substrate component as nimbus-aurora-keyhole.service). User-scope, reversible via restore.sh.
#
# This is the CONSUMER half. The producer half (the Rust daemon writing keyhole.json) is part of
# core-substrate; without it the plasmoid shows the honest UNKNOWN look (em-dashes), never a fake 0.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PKG="$HERE/package"
APPLET_ID="org.agentos.keyhole"
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
FEED="$RUNTIME/nimbus-aurora/keyhole.json"

# Desktop-tier component: on a headless / non-Plasma host kpackagetool6 is absent. Skip cleanly
# (exit 0) rather than failing the whole install — the keyhole is a Plasma 6 tray surface only.
if ! command -v kpackagetool6 >/dev/null 2>&1; then
  echo "• keyhole: kpackagetool6 not found (not a Plasma 6 desktop?) — skipping the tray plasmoid"
  exit 0
fi

[ -f "$PKG/metadata.json" ] || { echo "✗ keyhole: package not found at $PKG" >&2; exit 1; }

# Idempotent: --install errors if already present, so --upgrade an existing applet. This also
# repairs an older/stale copy (e.g. one installed straight from spikes/ that predates the
# porthole shader and shipped the test scaffolding).
if kpackagetool6 --type Plasma/Applet --list 2>/dev/null | grep -qx "$APPLET_ID"; then
  kpackagetool6 --type Plasma/Applet --upgrade "$PKG" >/dev/null
  echo "✓ keyhole plasmoid upgraded ($APPLET_ID)"
else
  kpackagetool6 --type Plasma/Applet --install "$PKG" >/dev/null
  echo "✓ keyhole plasmoid installed ($APPLET_ID)"
fi

# Gentle producer check — is something writing the feed the plasmoid reads?
if [ -f "$FEED" ]; then
  echo "  feed present: $FEED"
elif systemctl --user is-enabled nimbus-aurora-keyhole.service >/dev/null 2>&1; then
  echo "  producer enabled (nimbus-aurora-keyhole.service); feed appears once it ticks"
else
  echo "  ! no keyhole.json feed yet — install the 'core-substrate' component to run the producer,"
  echo "    or the tray will show the honest UNKNOWN look (em-dashes) until a feed exists"
fi

cat <<'EOF'
  → add it to your panel: right-click the system tray → Configure System Tray → Entries →
    set "AgentOS Keyhole" to Shown (or Auto). It idle-vanishes when there's nothing to report.
  → if you already had it placed, restart the shell to pick up this build:
    kquitapp6 plasmashell && (kstart plasmashell >/dev/null 2>&1 &)
EOF
