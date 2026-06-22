#!/usr/bin/env bash
# Thin compatibility wrapper — bring up the DEFAULT components (idempotent), same as before.
# The real work is now the component driver, so every capability is opt-in/opt-out:
#   ./install.sh                 interactive checklist
#   ./install.sh --only a,b      just these     ./install.sh --without comfyui   defaults minus
#   ./install.sh --all           everything     ./install.sh --list              the registry
#   ./uninstall.sh [...]         the aggregate reverse (mirror flags)
# Privileged steps (the Firefox root policy pin) are PRINTED at the end, never auto-escalated.
exec "$(cd "$(dirname "$0")" && pwd)/install.sh" --defaults "$@"
