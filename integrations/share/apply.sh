#!/usr/bin/env bash
# Install the AgentOS Share hub (ADR-0027) as a --user service: phone→box photo ingest on
# 127.0.0.1:8770, fronted tailnet-only by tailscale serve. Additive + reversible via restore.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT=agentos-share.service
UNIT_DIR="$HOME/.config/systemd/user"
PORT=8770

command -v python3 >/dev/null || { echo "✗ python3 not found" >&2; exit 1; }

mkdir -p "$UNIT_DIR"
install -m644 "$HERE/$UNIT" "$UNIT_DIR/$UNIT"

systemctl --user daemon-reload
if systemctl --user enable --now "$UNIT"; then
  echo "✓ Share hub installed + started → http://127.0.0.1:${PORT}"
  echo "  logs: journalctl --user -u $UNIT -f"
else
  echo "! could not enable the user service; start it by hand:" >&2
  echo "    systemctl --user enable --now $UNIT" >&2
fi

echo
echo "Optional follow-ups (not done automatically):"
echo "  • Hermes-chat door key:  cp $HERE/share.env.example ~/.config/agentos/share.env"
echo "      chmod 600 ~/.config/agentos/share.env; paste HERMES_API_KEY; then restart this unit."
echo "  • Expose tailnet-only:   $HERE/../agentosd-remote.sh up   (serves :$PORT)"
echo "  • Dream door needs the X-Share-Key hook:  systemctl --user restart agentos-lucid.service"
