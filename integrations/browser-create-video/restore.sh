#!/usr/bin/env bash
# Uninstall the browser "Create Video" native-messaging host (reverses apply.sh).
# Removes the installed native-host manifests from all browser dirs and the $HOME host copy.
# The tracked source is never touched by apply/restore now (it keeps its @LAUNCHER@ placeholder).
# The unpacked/temporary extension is removed BY YOU in the browser; the ROOT policy pin (if any)
# is reversed by a separate sudo step, which we surface below — we never escalate here.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOST_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/agentos"
HOST_SRC="$HOST_DIR/agentos_create_video_host.py"
MANIFEST_NAME="org.agentos.create_video.json"

for dest in \
  "$HOME/.config/mozilla/native-messaging-hosts" \
  "$HOME/.mozilla/native-messaging-hosts" \
  "$HOME/.var/app/org.mozilla.firefox/.mozilla/native-messaging-hosts" \
  "$HOME/snap/firefox/common/.mozilla/native-messaging-hosts" \
  "$HOME/.var/app/io.gitlab.librewolf-community/.librewolf/native-messaging-hosts" \
  "$HOME/.config/chromium/NativeMessagingHosts" \
  "$HOME/.config/google-chrome/NativeMessagingHosts"; do
  rm -f "$dest/$MANIFEST_NAME"
done
echo "✓ native-host manifests removed (Firefox XDG/legacy/Flatpak/Snap, LibreWolf, Chromium, Chrome)"

# Remove the installed host copy (and the agentos data dir if we left it empty).
rm -f "$HOST_SRC"
rmdir "$HOST_DIR" 2>/dev/null || true
echo "✓ installed host removed → $HOST_SRC"

# Surface the privileged residue: if Firefox is still force-pinned by our policy, the only way to
# remove it is a root step. We print it (copy-don't-execute), never sudo here.
if grep -qs 'create-video@agentos' /etc/firefox/policies/policies.json 2>/dev/null; then
  echo
  echo "! Firefox is still force-installed via the root policy. To fully remove it:"
  echo "    sudo $HERE/policy/restore-policy.sh"
fi

echo "  Note: remove any unpacked extension yourself —"
echo "    Firefox: about:debugging → Remove (temporary add-ons also clear on restart)"
echo "    Chrome : chrome://extensions → Remove on the AgentOS card"
