#!/usr/bin/env bash
# agentosd-remote.sh — expose the AgentOS web UIs to your tailnet, securely.
#
# Secure-by-design: each service stays bound to 127.0.0.1; `tailscale serve` fronts it
# over HTTPS, reachable ONLY by your authenticated tailnet devices. This never opens a
# public port. ComfyUI (8188) and the Hermes REST API (8642) are deliberately NOT exposed.
# See docs/REMOTE-ACCESS.md. Run AFTER `sudo tailscale up` (and joining your phone).
#
# usage:  agentosd-remote.sh {up|down|status}
#
# Note: `tailscale serve` may need root unless you've run `sudo tailscale set
# --operator=$USER`. If a serve call fails with a permission error, re-run with sudo.
set -euo pipefail

# UIs safe to expose (port -> label). Edit to taste.
PORTS=(8765 9123 9124 9119 8770)
declare -A LABEL=(
  [8765]="Lucid — dream UI"
  [9123]="status panel"
  [9124]="models panel"
  [9119]="Hermes board"
  [8770]="AgentOS Share — phone ingest hub"
)

_need_tailscale() {
  command -v tailscale >/dev/null 2>&1 || {
    echo "tailscale not installed. Install it first — see docs/REMOTE-ACCESS.md" >&2; exit 1; }
  if ! tailscale status >/dev/null 2>&1; then
    echo "This box is not on a tailnet. Run:  sudo tailscale up   then re-run this." >&2; exit 1
  fi
}

_dns_name() {
  tailscale status --json 2>/dev/null \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null \
    || echo "<your-box>.<tailnet>.ts.net"
}

up() {
  _need_tailscale
  for p in "${PORTS[@]}"; do
    echo "serving :$p  (${LABEL[$p]:-})"
    tailscale serve --bg --https="$p" "http://127.0.0.1:$p"
  done
  local host; host="$(_dns_name)"
  echo
  echo "Reachable from your tailnet devices (phone on the same account):"
  for p in "${PORTS[@]}"; do printf '  https://%s:%s/   %s\n' "$host" "$p" "${LABEL[$p]:-}"; done
  echo
  echo "Not exposed (by design): 8188 ComfyUI, 8642 Hermes REST API."
}

down() {
  _need_tailscale
  for p in "${PORTS[@]}"; do
    echo "stopping :$p"
    tailscale serve --https="$p" off 2>/dev/null || true
  done
  echo "done. (box stays on the tailnet; 'sudo tailscale down' to leave entirely;"
  echo " 'tailscale serve reset' clears ALL serve mappings if a port won't clear.)"
}

status() {
  _need_tailscale
  echo "tailnet name: $(_dns_name)"
  echo
  tailscale serve status 2>/dev/null || echo "(no serve mappings)"
}

case "${1:-}" in
  up)     up ;;
  down)   down ;;
  status) status ;;
  *) echo "usage: $0 {up|down|status}"; exit 2 ;;
esac
