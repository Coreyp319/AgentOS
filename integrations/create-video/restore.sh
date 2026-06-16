#!/usr/bin/env bash
# Uninstall the "Create" Dolphin ServiceMenu (reverses apply.sh).
set -euo pipefail

MENU="$HOME/.local/share/kio/servicemenus/agentos-create-video.desktop"

rm -f "$MENU"
echo "✓ Create ServiceMenu removed"
