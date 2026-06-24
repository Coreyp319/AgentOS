#!/usr/bin/env bash
# Install the KRunner "Ask Claude / Ask Hermes / Search the web" D-Bus runner.
#
# We do NOT vendor pack files (CLAUDE.md): the runner is authored and maintained in the
# Nimbus pack (the separate desktop repo). This component just drives the pack's own
# user-scope installer so the runner becomes a first-class, registry-managed AgentOS option
# that the install checklist + the status-page adopt webui (ADR-0043) can turn on or off.
# Honors "don't reinvent": one source of truth (the pack), one switch (here).
#
# User-scope only — the pack's claude-runner installer needs no sudo (it templates the
# detected `claude` CLI and a pulled hermes* Ollama model, and hides each row if absent;
# DuckDuckGo/GitHub/Wikipedia/YouTube web search always works). The sudo-gated row-styling
# tweak in the same pack module is intentionally NOT touched here.
set -euo pipefail

# Where the Nimbus pack lives. CLAUDE.md pins it at ~/whitesur-cachyos-pack; override with
# NIMBUS_PACK for a non-standard checkout. We only ever read the pack — never copy from it.
PACK="${NIMBUS_PACK:-$HOME/whitesur-cachyos-pack}"
RUNNER_INSTALL="$PACK/3-krunner-finder/claude-runner/install.sh"

if [ ! -f "$RUNNER_INSTALL" ]; then
  echo "✗ krunner-finder: Nimbus pack not found at $PACK"
  echo "  Expected the runner installer at: $RUNNER_INSTALL"
  echo "  This component drives the pack's own installer (we vendor no pack files)."
  echo "  Clone/locate the pack, or point NIMBUS_PACK=/path/to/whitesur-cachyos-pack and re-run."
  exit 1
fi

echo "→ krunner-finder: running the pack's user-scope runner installer ($RUNNER_INSTALL)"
bash "$RUNNER_INSTALL"
echo "✓ krunner-finder installed. Open KRunner (Alt+Space): the Ask Claude / Ask Hermes / Search"
echo "  rows appear when nothing else matches, or instantly via the  c …  /  h …  /  s …  prefixes."
echo "  (needs python-dbus + python-gobject — preflight flags them if missing)"
