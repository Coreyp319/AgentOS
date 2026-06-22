#!/usr/bin/env bash
# AgentOS component UNINSTALLER — the aggregate reverse of install.sh (closes the "apply-all has no
# restore-all" gap). Reads components.conf and restores the selected components in REVERSE order.
#   ./uninstall.sh                    # interactive checklist
#   ./uninstall.sh --defaults         # reverse the default stack
#   ./uninstall.sh --all              # remove everything the registry knows
#   ./uninstall.sh --only firefox-pin
# Privileged reverses (e.g. the Firefox root policy) are printed, never auto-run.
export MODE=uninstall
source "$(cd "$(dirname "$0")" && pwd)/_driver.sh"
