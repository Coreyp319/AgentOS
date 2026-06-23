#!/usr/bin/env bash
# Reverse apply.sh: disable + remove the gpu-coordinator Hermes plugin, revert the config edit,
# and restart the gateway. Clean inverse of apply.sh.
#
# OUT OF SCOPE (apply.sh never touched these, so restore must not either): the API_SERVER_*
# REST binding in ~/.hermes/.env and `hermes mcp add agentos` stay manual — reverse them by hand
# per $HERE/DEPLOY.md if you set them up. Reverting them here would under-/over-reach.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
NAME="gpu-coordinator"
DEST="$HERMES_HOME/plugins/$NAME"
CFG="$HERMES_HOME/config.yaml"

# The EXACTLY 5 files apply.sh installs — deleted individually (never `rm -rf` a globbed dir),
# so we only remove what we put there, then drop the dir if it is now empty.
FILES=(__init__.py coordinator.py lease_client.py lease_state.py plugin.yaml)

# --- disable via the hermes CLI if present ---------------------------------------------------
command -v hermes >/dev/null 2>&1 && hermes plugins disable "$NAME" 2>/dev/null \
  && echo "✓ disabled via hermes CLI" || true

# --- delete the 5 installed files, then the dir if empty -------------------------------------
if [ -d "$DEST" ]; then
  for f in "${FILES[@]}"; do
    rm -f "$DEST/$f"
  done
  rm -f "$DEST"/__pycache__/*.pyc 2>/dev/null || true
  rmdir "$DEST/__pycache__" 2>/dev/null || true
  rmdir "$DEST" 2>/dev/null || true
  echo "✓ removed plugin files ${FILES[*]} from $DEST"
else
  echo "($NAME plugin dir not present — nothing to remove)"
fi

# --- revert the config.yaml enable (must-fix #12: prefer surgical removal; else disclose) -----
# PREFERRED: surgically excise ONLY the exact block apply.sh appended via guard-append, so later
# user edits to config.yaml are preserved. apply.sh appends EXACTLY (and only when no 'plugins:'
# block existed):
#     <blank line>
#     plugins:
#       enabled:
#         - gpu-coordinator
# We remove only that trailing 4-line block, and only if it is verbatim the LAST thing in the
# file (proving it is our append, not a hand-authored plugins: block we must not touch).
_surgical_removed=0
if [ -f "$CFG" ]; then
  # Build the exact appended block (matches apply.sh's printf) and compare it to the file's tail.
  block="$(printf '\nplugins:\n  enabled:\n    - %s\n' "$NAME")"
  blines="$(printf '%s' "$block" | wc -l)"   # number of newline-terminated lines in the block
  ftail="$(tail -n "$blines" "$CFG" 2>/dev/null || true)"
  # tail() drops the block's leading blank line; compare against the block sans that blank line.
  block_no_lead="$(printf 'plugins:\n  enabled:\n    - %s\n' "$NAME")"
  if [ "$ftail" = "$(printf '%s' "$block_no_lead")" ]; then
    total="$(wc -l < "$CFG")"
    keep=$(( total - blines ))
    tmp="$(mktemp "${CFG}.agentos-restore.XXXXXX")"
    # Keep everything before our block; also trim the single blank separator line apply.sh added.
    head -n "$keep" "$CFG" > "$tmp"
    # Drop a trailing blank line if our append left one (apply.sh prepended '\n').
    sed -i -e '${/^$/d}' "$tmp" 2>/dev/null || true
    mv -f "$tmp" "$CFG"
    _surgical_removed=1
    echo "✓ surgically removed the appended plugins.enabled: [$NAME] block from $CFG (later edits preserved)"
  fi
fi

# Fallback: whole-file restore from the snapshot. LOSSY — reverts the ENTIRE config.yaml to its
# pre-adopt state, discarding any edits made after apply. We say so clearly; we never silently clobber.
if [ "$_surgical_removed" -eq 0 ]; then
  if [ -f "$CFG.agentos-bak" ]; then
    echo "! could not surgically remove the [$NAME] enable block (config.yaml was edited after apply,"
    echo "  or it was enabled differently). Restoring the WHOLE config.yaml from the pre-adopt snapshot —"
    echo "  this REVERTS config.yaml entirely to its pre-adopt state and DISCARDS any edits made since."
    mv -f "$CFG.agentos-bak" "$CFG" && echo "✓ restored $CFG from $CFG.agentos-bak (whole-file)"
  else
    echo "note: no [$NAME] block at the tail of $CFG and no snapshot found — if you enabled it by"
    echo "      editing config.yaml, remove '$NAME' from plugins.enabled there by hand."
  fi
fi

# On a successful surgical removal the snapshot is no longer needed — drop it so a future re-apply
# takes a fresh create-if-absent backup of the current (correct) config.
if [ "$_surgical_removed" -eq 1 ] && [ -f "$CFG.agentos-bak" ]; then
  rm -f "$CFG.agentos-bak" && echo "✓ removed stale snapshot $CFG.agentos-bak"
fi

# --- restart the gateway so it drops the plugin (is-active-guarded) ---------------------------
if systemctl --user is-active --quiet hermes-gateway.service 2>/dev/null; then
  systemctl --user restart hermes-gateway.service && echo "✓ hermes-gateway restarted"
fi

echo
echo "note: the REST control API (API_SERVER_* in ~/.hermes/.env) and 'hermes mcp add agentos'"
echo "      were never touched by apply.sh — if you set them up by hand, reverse them per $HERE/DEPLOY.md."
