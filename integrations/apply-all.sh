#!/usr/bin/env bash
# Bring up the AgentOS boot stack pieces that aren't already installed:
#   * Hermes web dashboard      (http://127.0.0.1:9119)
#   * ComfyUI dreaming backend  (http://127.0.0.1:8188)
#   * AgentOS status panel      (http://127.0.0.1:9123) — opens at login, the front door
# The Hermes gateway daemon and Ollama are already installed/enabled — left untouched.
# Idempotent: re-running just re-asserts the units. Reverse with each dir's restore.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

echo "→ Hermes dashboard…"
"$HERE/hermes/dashboard/apply.sh"

echo "→ ComfyUI…"
"$HERE/comfyui/apply.sh"

echo "→ AgentOS status panel…"
"$HERE/status-panel/apply.sh"

echo
echo "Boot stack:"
systemctl --user --no-pager --no-legend list-units \
  hermes-gateway.service hermes-dashboard.service comfyui.service agentos-status-panel.service 2>/dev/null \
  | sed 's/^/  /' || true
