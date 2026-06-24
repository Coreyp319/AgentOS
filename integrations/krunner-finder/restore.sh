#!/usr/bin/env bash
# Remove the KRunner "Ask Claude / Ask Hermes / Search the web" D-Bus runner.
#
# Self-contained on PURPOSE: it mirrors the claude-runner teardown without reaching back into
# the pack, so you can uninstall this component cleanly even if the pack was moved or removed
# after install. It reverses ONLY the user-scope runner — it never touches the pack's
# sudo-gated row-styling tweak (that one is reverted from the pack with sudo, by hand).
set -uo pipefail

SVC="dev.corey.krunner.claude"   # the pack's fixed D-Bus namespace for the runner

# Hide the runner in KRunner, then drop its activation + deploy artifacts.
kwriteconfig6 --file krunnerrc --group Plugins --key claudesearchEnabled false 2>/dev/null || true
rm -f "$HOME/.local/share/dbus-1/services/$SVC.service" \
      "$HOME/.local/share/krunner/dbusplugins/$SVC.desktop"
rm -rf "$HOME/.local/share/krunner-claude-runner"

# Stop a live D-Bus-activated copy (kquitapp6 only quits krunner itself) and reload KRunner.
pkill -f 'python3.*claude_runner.py' 2>/dev/null || true
kquitapp6 krunner 2>/dev/null || true

echo "✓ krunner-finder removed — KRunner is back to stock (the Ask Claude/Hermes/Search rows are gone)."
