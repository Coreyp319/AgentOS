#!/usr/bin/env bash
# Standard apply alias so the component driver finds the Hermes-plugin install via the
# apply.sh/restore.sh convention. Installs the needs-you-signal plugin (observer-only, safe).
# The gpu-coordinator plugin is now its OWN component (gpu-coordinator/apply.sh) — adopt it there.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
"$HERE/install.sh" "$@"
