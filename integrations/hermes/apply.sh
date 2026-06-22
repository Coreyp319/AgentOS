#!/usr/bin/env bash
# Standard apply alias so the component driver finds the Hermes-plugin install via the
# apply.sh/restore.sh convention. Installs the needs-you-signal plugin (observer-only, safe).
# The gpu-coordinator plugin is a separate, sensitive Hermes-side deploy — surfaced, not auto-run.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
"$HERE/install.sh" "$@"
if [ ! -d "${HERMES_HOME:-$HOME/.hermes}/plugins/gpu-coordinator" ]; then
  echo
  echo "! gpu-coordinator plugin (holds the VRAM lease around Hermes inference) is NOT installed."
  echo "  It's a sensitive Hermes-side deploy — follow $HERE/gpu-coordinator/DEPLOY.md by hand."
fi
