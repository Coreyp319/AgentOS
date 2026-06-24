#!/usr/bin/env bash
# Install the "Create" Dolphin ServiceMenu — right-click an image → Create → two i2v items,
# both routed through the governed launcher (create_from_image.py). Reversible via restore.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# Resolve the launcher's absolute dir from the repo layout (never hard-code /home/corey).
LUCID_DIR="$(cd "$HERE/../../apps/dreaming/lucid" && pwd)"
TEMPLATE="$LUCID_DIR/dist/agentos-create-video.desktop.in"

MENU_DIR="$HOME/.local/share/kio/servicemenus"   # kservices5/ServiceMenus is DEAD on Plasma 6.
MENU="$MENU_DIR/agentos-create-video.desktop"

command -v python3 >/dev/null || { echo "✗ python3 not found" >&2; exit 1; }
[ -f "$LUCID_DIR/create_from_image.py" ] || { echo "✗ launcher missing: $LUCID_DIR/create_from_image.py" >&2; exit 1; }
[ -f "$TEMPLATE" ] || { echo "✗ template missing: $TEMPLATE" >&2; exit 1; }

mkdir -p "$MENU_DIR"
sed "s|@LUCID_DIR@|$LUCID_DIR|g" "$TEMPLATE" > "$MENU"
chmod +x "$MENU"   # the user servicemenus dir only authorizes executable .desktop files.

echo "✓ ServiceMenu installed → $MENU"
echo "  launcher: $LUCID_DIR/create_from_image.py"
echo "  verify: right-click a PNG/JPG in Dolphin → Create → Create Video from Image[ (Private)]"
echo "  if it doesn't show, restart Dolphin; fallback: kbuildsycoca6"
