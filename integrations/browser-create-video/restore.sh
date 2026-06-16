#!/usr/bin/env bash
# Uninstall the browser "Create Video" native-messaging host (reverses apply.sh).
# Removes the installed native-host manifests from all three browser dirs and resets the
# committed host script's baked launcher path back to its @LAUNCHER@ placeholder (clean repo).
# The unpacked extension is removed BY YOU in the browser (see below) — we can't unload it.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOST_SRC="$HERE/host/agentos_create_video_host.py"
MANIFEST_NAME="org.agentos.create_video.json"

for dest in \
  "$HOME/.mozilla/native-messaging-hosts" \
  "$HOME/.config/chromium/NativeMessagingHosts" \
  "$HOME/.config/google-chrome/NativeMessagingHosts"; do
  rm -f "$dest/$MANIFEST_NAME"
done
echo "✓ native-host manifests removed (Firefox / Chromium / Chrome)"

# Reset the baked launcher path so the working tree returns to the committed placeholder.
if [ -f "$HOST_SRC" ]; then
  python3 - "$HOST_SRC" <<'PY'
import re, sys
host = sys.argv[1]
src = open(host, encoding="utf-8").read()
src, _ = re.subn(r'^LAUNCHER = ".*"$', 'LAUNCHER = "@LAUNCHER@"', src, count=1, flags=re.M)
open(host, "w", encoding="utf-8").write(src)
PY
  echo "✓ host script launcher path reset to @LAUNCHER@ placeholder"
fi

echo "  Note: remove the unpacked extension yourself —"
echo "    Firefox: about:debugging → Remove (temporary add-ons also clear on restart)"
echo "    Chrome : chrome://extensions → Remove on the AgentOS card"
