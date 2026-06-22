#!/usr/bin/env bash
# AgentOS component installer. Reads components.conf and brings up the selected components.
#   ./install.sh                      # interactive checklist (default-on preselected)
#   ./install.sh --defaults           # the default local stack, non-interactive
#   ./install.sh --only lucid,share-hub
#   ./install.sh --without comfyui --yes
#   ./install.sh --list               # show the registry
# Reverse with ./uninstall.sh (same flags). Privileged steps are printed, never auto-run.
export MODE=install
source "$(cd "$(dirname "$0")" && pwd)/_driver.sh"
