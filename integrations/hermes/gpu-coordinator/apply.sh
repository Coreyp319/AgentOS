#!/usr/bin/env bash
# Install + enable the gpu-coordinator Hermes plugin (ADR-0006/0010/0041), promoting the
# hand-run DEPLOY.md into a one-click `components.conf` component. Clones the structure of
# hermes/install.sh (the needs-you-signal template) for the parts it shares.
#
# What this plugin does: it registers ONE `llm_execution` middleware that wraps every Hermes
# inference in the agentosd interactive/batch VRAM lease, so a live AI turn preempts the
# overnight dream/batch lane. Refcounted + fail-open + opt-in.
#
# IN SCOPE (this component owns + reverses these): the 5 plugin files + the plugins.enabled
# config edit + the gateway restart.
# OUT OF SCOPE — stay MANUAL, deliberately NOT encoded here so restore.sh stays a clean inverse:
#   * the Hermes REST control API binding (API_SERVER_* in ~/.hermes/.env)
#   * `hermes mcp add agentos` (the read-only GPU-introspection MCP server)
#   Both are documented in DEPLOY.md ("(Optional) …" sections); fold neither in.
#
# NOTE: enabling a plugin requires restarting hermes-gateway, which briefly interrupts any
# running Hermes agents. Run this when the fleet is idle. Reversible via restore.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
NAME="gpu-coordinator"
DEST="$HERMES_HOME/plugins/$NAME"
CFG="$HERMES_HOME/config.yaml"

# The EXACTLY 5 named plugin files (DEPLOY.md §"Install the plugin"). Enumerated, never globbed,
# so a stray file in the source dir can never be silently shipped into the live Hermes install.
FILES=(__init__.py coordinator.py lease_client.py lease_state.py plugin.yaml)

# --- soft jeepney preflight (honesty only — NEVER exit 1; the plugin is fail-open) -----------
# jeepney is the persistent D-Bus transport (DEPLOY.md): with it the cooperative lease survives
# across calls; without it the plugin degrades to the ephemeral `busctl` transport, which churns
# the lease so the hold never persists and the keyhole never shows it. Still fully functional and
# fail-open — so this only WARNS, it never blocks the install.
HVENV_PY="$HERMES_HOME/hermes-agent/venv/bin/python"
if [ -x "$HVENV_PY" ]; then
  if ! "$HVENV_PY" -c 'import jeepney' >/dev/null 2>&1; then
    echo "! jeepney is not importable in the Hermes venv — the plugin will degrade to the"
    echo "  ephemeral 'busctl' transport: still fail-open + functional, but the cooperative lease"
    echo "  churns (the hold never persists; the keyhole won't show it). For the persistent path:"
    echo "    uv pip install --python $HVENV_PY jeepney"
  else
    echo "✓ jeepney importable in the Hermes venv (persistent lease transport available)"
  fi
else
  echo "! could not find the Hermes venv python ($HVENV_PY) to check for jeepney —"
  echo "  the plugin still installs + runs fail-open; install jeepney into the Hermes venv for the"
  echo "  persistent lease path (see $HERE/DEPLOY.md)."
fi

# --- install the 5 files ---------------------------------------------------------------------
mkdir -p "$DEST"
for f in "${FILES[@]}"; do
  install -m644 "$HERE/$f" "$DEST/$f"
done
echo "✓ plugin files (${FILES[*]}) → $DEST"

# --- enable: prefer the hermes CLI; else guard-append to config.yaml (mirrors hermes/install.sh) ---
if command -v hermes >/dev/null 2>&1; then
  hermes plugins enable "$NAME" && echo "✓ enabled via hermes CLI" \
    || echo "! 'hermes plugins enable $NAME' failed — enable it by hand"
else
  if grep -q "$NAME" "$CFG" 2>/dev/null; then
    echo "✓ $NAME already referenced in $CFG"
  elif grep -qE '^plugins:' "$CFG" 2>/dev/null; then
    echo "! a 'plugins:' block already exists in $CFG — add '$NAME' to plugins.enabled by hand"
  else
    # Snapshot the pre-edit config ONCE *before* mutating, create-if-absent ONLY (must-fix #12):
    # a second apply must NOT overwrite a good backup with already-mutated config. Abort the
    # append if the backup fails — never mutate state we can't reverse. (Mirrors install.sh:33-37.)
    if [ ! -e "$CFG.agentos-bak" ]; then
      cp -a "$CFG" "$CFG.agentos-bak" || { echo "✗ could not back up $CFG — not appending; add '$NAME' by hand" >&2; exit 1; }
    fi
    printf '\nplugins:\n  enabled:\n    - %s\n' "$NAME" >> "$CFG"
    echo "✓ added plugins.enabled: [$NAME] → $CFG (backed up → $CFG.agentos-bak)"
  fi
fi

# --- restart the gateway so discovery picks up the plugin (is-active-guarded) -----------------
if systemctl --user is-active --quiet hermes-gateway.service 2>/dev/null; then
  systemctl --user restart hermes-gateway.service && echo "✓ hermes-gateway restarted"
else
  echo "! hermes-gateway.service not active under --user — restart it however it runs so the plugin loads"
fi

echo
echo "verify:  hermes plugins list   |   grep gpu-coordinator \"$HERMES_HOME/logs/agent.log\" | tail -1"
echo "note: the REST control API (API_SERVER_* in ~/.hermes/.env) and 'hermes mcp add agentos'"
echo "      remain MANUAL (see $HERE/DEPLOY.md) — this component does not touch them."
