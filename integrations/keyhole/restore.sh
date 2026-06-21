#!/usr/bin/env bash
# Remove the AgentOS Keyhole tray plasmoid (reverses apply.sh). User-scope.
# Leaves the producer (nimbus-aurora-keyhole.service, owned by core-substrate) untouched —
# this component is only the consumer/UI half.
set -euo pipefail

APPLET_ID="org.agentos.keyhole"

if ! command -v kpackagetool6 >/dev/null 2>&1; then
  echo "• keyhole: kpackagetool6 not found — nothing to remove"
  exit 0
fi

if kpackagetool6 --type Plasma/Applet --list 2>/dev/null | grep -qx "$APPLET_ID"; then
  kpackagetool6 --type Plasma/Applet --remove "$APPLET_ID" >/dev/null
  echo "✓ keyhole plasmoid removed ($APPLET_ID)"
  echo "  if it was placed in your tray, restart the shell to clear it:"
  echo "    kquitapp6 plasmashell && (kstart plasmashell >/dev/null 2>&1 &)"
else
  echo "• keyhole plasmoid not installed — nothing to remove"
fi
