#!/usr/bin/env bash
# Install the browser "Create Video from Image" native-messaging host. The WebExtension itself
# is loaded UNPACKED by hand (see below) — this script only wires the native host that bridges
# the extension to the governed launcher (create_from_image.py). Reversible via restore.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOST_SRC="$(cd "$HERE/host" && pwd)/agentos_create_video_host.py"
# Resolve the launcher's absolute path from the repo layout (never hard-code /home/corey).
LAUNCHER="$(cd "$HERE/../../spikes/dreaming/lucid" && pwd)/create_from_image.py"

command -v python3 >/dev/null || { echo "✗ python3 not found" >&2; exit 1; }
[ -f "$LAUNCHER" ]  || { echo "✗ launcher missing: $LAUNCHER" >&2; exit 1; }
[ -f "$HOST_SRC" ]  || { echo "✗ host script missing: $HOST_SRC" >&2; exit 1; }

# Bake the launcher path into the host script so native messaging can exec it standalone.
# Rewrite the WHOLE LAUNCHER = "…" line every run (matches @LAUNCHER@ or any previously-baked
# path), so re-running, or a different checkout location, always lands the current path.
# restore.sh resets it back to the @LAUNCHER@ placeholder to keep the repo clean.
python3 - "$HOST_SRC" "$LAUNCHER" <<'PY'
import re, sys
host, launcher = sys.argv[1], sys.argv[2]
src = open(host, encoding="utf-8").read()
# Re-quote the path safely for a Python string literal (json.dumps gives a valid one).
import json
new = "LAUNCHER = " + json.dumps(launcher)
src, n = re.subn(r'^LAUNCHER = ".*"$', new, src, count=1, flags=re.M)
if n != 1:
    sys.exit("could not find the LAUNCHER assignment line to template")
open(host, "w", encoding="utf-8").write(src)
PY
chmod +x "$HOST_SRC"
echo "✓ host script wired → $HOST_SRC"
echo "  launcher: $LAUNCHER"

FF_TPL="$HERE/host/org.agentos.create_video.firefox.json.in"
CR_TPL="$HERE/host/org.agentos.create_video.chrome.json.in"
MANIFEST_NAME="org.agentos.create_video.json"

# Per-browser native-messaging-host directories. We install ONLY where the browser's parent
# config dir already exists (i.e. the browser is present) — missing browsers are skipped, not created.
declare -A BROWSERS=(
  ["Firefox"]="$HOME/.mozilla|$HOME/.mozilla/native-messaging-hosts|firefox"
  ["Chromium"]="$HOME/.config/chromium|$HOME/.config/chromium/NativeMessagingHosts|chrome"
  ["Chrome"]="$HOME/.config/google-chrome|$HOME/.config/google-chrome/NativeMessagingHosts|chrome"
)

installed_any=0
for name in "${!BROWSERS[@]}"; do
  IFS='|' read -r parent dest flavor <<< "${BROWSERS[$name]}"
  if [ ! -d "$parent" ]; then
    echo "! $name not detected ($parent absent) — skipping"
    continue
  fi
  mkdir -p "$dest"
  if [ "$flavor" = "firefox" ]; then
    sed "s|@HOST@|$HOST_SRC|g" "$FF_TPL" > "$dest/$MANIFEST_NAME"
    echo "✓ $name native host → $dest/$MANIFEST_NAME"
  else
    # Chrome/Chromium pin the host to a specific extension ID. We leave @CHROME_EXTENSION_ID@
    # in place — the user pastes their unpacked extension's ID (chrome://extensions) and re-runs,
    # or edits the installed manifest directly. Firefox needs no such step (it matches by id).
    sed "s|@HOST@|$HOST_SRC|g" "$CR_TPL" > "$dest/$MANIFEST_NAME"
    echo "✓ $name native host → $dest/$MANIFEST_NAME"
    echo "    ! Chrome/Chromium: edit that file and replace @CHROME_EXTENSION_ID@ with your"
    echo "      unpacked extension's ID (chrome://extensions → Developer mode → the ID under the card)."
  fi
  installed_any=1
done

[ "$installed_any" = 1 ] || echo "! no supported browser config dirs found — nothing installed."

echo
echo "Next steps — load the unpacked extension:"
echo "  Firefox : about:debugging#/runtime/this-firefox → Load Temporary Add-on → pick"
echo "            $HERE/extension/manifest.json"
echo "  Chrome  : chrome://extensions → enable Developer mode → Load unpacked → pick"
echo "            $HERE/extension/  (then copy the ID into the manifest above and re-run apply.sh)"
echo
echo "Then right-click any image on a web page → Create Video from Image[ (Private)]."
echo "Safety (B2 consent gate, lease, private mode) all live in the launcher — see README.md."
