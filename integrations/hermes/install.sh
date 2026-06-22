#!/usr/bin/env bash
# Install the needs-you-signal Hermes plugin (AgentOS P2).
# Observer-only: writes ~/.hermes/needs_you.json on approval events; never vetoes.
# Reversible via uninstall.sh.
#
# NOTE: enabling a plugin requires restarting hermes-gateway, which briefly
# interrupts any running Hermes agents. Run this when the fleet is idle.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
NAME="needs-you-signal"
DEST="$HERMES_HOME/plugins/$NAME"

mkdir -p "$DEST"
install -m644 "$HERE/$NAME/plugin.yaml" "$DEST/plugin.yaml"
install -m644 "$HERE/$NAME/__init__.py" "$DEST/__init__.py"
echo "✓ plugin files → $DEST"

# Enable it (prefer the hermes CLI; else guard-append to config.yaml).
if command -v hermes >/dev/null 2>&1; then
  hermes plugins enable "$NAME" && echo "✓ enabled via hermes CLI" \
    || echo "! 'hermes plugins enable $NAME' failed — enable it by hand"
else
  CFG="$HERMES_HOME/config.yaml"
  if grep -q "$NAME" "$CFG" 2>/dev/null; then
    echo "✓ $NAME already referenced in $CFG"
  elif grep -qE '^plugins:' "$CFG" 2>/dev/null; then
    echo "! a 'plugins:' block already exists in $CFG — add '$NAME' to plugins.enabled by hand"
  else
    # Snapshot the pre-edit config ONCE *before* mutating, and abort the append if the backup fails —
    # never mutate state we can't reverse.
    if [ ! -e "$CFG.agentos-bak" ]; then
      cp -a "$CFG" "$CFG.agentos-bak" || { echo "✗ could not back up $CFG — not appending; add '$NAME' by hand" >&2; exit 1; }
    fi
    printf '\nplugins:\n  enabled:\n    - %s\n' "$NAME" >> "$CFG"
    echo "✓ added plugins.enabled: [$NAME] → $CFG (backed up → $CFG.agentos-bak)"
  fi
fi

# Restart the gateway so discovery picks up the new plugin.
if systemctl --user is-active --quiet hermes-gateway.service 2>/dev/null; then
  systemctl --user restart hermes-gateway.service && echo "✓ hermes-gateway restarted"
else
  echo "! hermes-gateway.service not active under --user — restart it however it runs so the plugin loads"
fi

echo "verify:  hermes plugins list   |   cat \"$HERMES_HOME/needs_you.json\""
