#!/usr/bin/env bash
# Install the browser "Create Video from Image" native-messaging host. The WebExtension itself
# is loaded UNPACKED by hand (see below) — this script only wires the native host that bridges
# the extension to the governed launcher (create_from_image.py). Reversible via restore.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOST_REPO="$(cd "$HERE/host" && pwd)/agentos_create_video_host.py"
# Resolve the launcher's absolute path from the repo layout (never hard-code /home/corey).
LAUNCHER="$(cd "$HERE/../../apps/dreaming/lucid" && pwd)/create_from_image.py"
# The INSTALLED host lives in $HOME, never the repo — so installing never dirties the tracked
# source (which keeps its @LAUNCHER@ placeholder). restore.sh just rm's this copy.
HOST_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/agentos"
HOST_SRC="$HOST_DIR/agentos_create_video_host.py"

command -v python3 >/dev/null || { echo "✗ python3 not found" >&2; exit 1; }
[ -f "$LAUNCHER" ]  || { echo "✗ launcher missing: $LAUNCHER" >&2; exit 1; }
[ -f "$HOST_REPO" ] || { echo "✗ host script missing: $HOST_REPO" >&2; exit 1; }

# Install the host into $HOME, substituting the launcher path into the COPY. The tracked source is
# left untouched (machine-independent, clean working tree); the browsers' native-host manifests below
# point at this $HOME copy.
mkdir -p "$HOST_DIR"
python3 - "$HOST_REPO" "$HOST_SRC" "$LAUNCHER" <<'PY'
import json, re, sys
repo, dest, launcher = sys.argv[1], sys.argv[2], sys.argv[3]
src = open(repo, encoding="utf-8").read()
src, n = re.subn(r'^LAUNCHER = ".*"$', "LAUNCHER = " + json.dumps(launcher), src, count=1, flags=re.M)
if n != 1:
    sys.exit("could not find the LAUNCHER assignment line to template")
open(dest, "w", encoding="utf-8").write(src)
PY
chmod +x "$HOST_SRC"
echo "✓ host installed → $HOST_SRC  (tracked source untouched)"
echo "  launcher: $LAUNCHER"

FF_TPL="$HERE/host/org.agentos.create_video.firefox.json.in"
CR_TPL="$HERE/host/org.agentos.create_video.chrome.json.in"
MANIFEST_NAME="org.agentos.create_video.json"

# Per-browser native-messaging-host directories. We install ONLY where the browser's base config
# dir already exists (i.e. the browser is present) — missing browsers are skipped, not created.
# Firefox has SEVERAL possible homes: modern Firefox (~128+) moved its profile to the XDG path
# $HOME/.config/mozilla (NOT $HOME/.mozilla), and Flatpak/Snap builds live under their own roots —
# cover them all, else a recent Firefox is silently skipped (the bug this fixes).
declare -A BROWSERS=(
  ["Firefox (XDG)"]="$HOME/.config/mozilla|$HOME/.config/mozilla/native-messaging-hosts|firefox"
  ["Firefox (legacy)"]="$HOME/.mozilla|$HOME/.mozilla/native-messaging-hosts|firefox"
  ["Firefox (Flatpak)"]="$HOME/.var/app/org.mozilla.firefox|$HOME/.var/app/org.mozilla.firefox/.mozilla/native-messaging-hosts|firefox"
  ["Firefox (Snap)"]="$HOME/snap/firefox|$HOME/snap/firefox/common/.mozilla/native-messaging-hosts|firefox"
  ["LibreWolf (Flatpak)"]="$HOME/.var/app/io.gitlab.librewolf-community|$HOME/.var/app/io.gitlab.librewolf-community/.librewolf/native-messaging-hosts|firefox"
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
