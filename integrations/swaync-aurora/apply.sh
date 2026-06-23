#!/usr/bin/env bash
# AgentOS — install the Aurora "instrument glass" swaync style.
# Replaces the shipped macOS-mimic ~/.config/swaync/style.css (Apple blue/green,
# SF Pro) with the AgentOS instrument register: deep-navy glass, ONE violet
# accent, calm. User-scope, reversible via restore.sh, OFF by default (opt-in;
# the parent registers this in components.conf).
#
# NEVER launches swaync. If swaync is already running we hot-reload its CSS;
# if it is NOT running we only install the file and tell you when it takes
# effect (next swaync start). Idempotent. ADR-0005 (reversible).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CFG_DIR="$HOME/.config/swaync"
LIVE="$CFG_DIR/style.css"
BACKUP="$CFG_DIR/style.css.preaurora"
SRC="$HERE/style.css"

command -v swaync >/dev/null 2>&1 \
  || echo "! swaync not found on PATH — installing the style anyway; it applies when swaync runs." >&2

mkdir -p "$CFG_DIR"

# 1. Back up the existing style ONCE (preserve the original macOS-mimic / user
#    customisation). Only if a real file is present AND we haven't backed up yet.
#    Skip if the live file is already OURS (idempotent re-apply must not back up
#    our own style over the user's original).
if [ -e "$LIVE" ] && [ ! -e "$BACKUP" ]; then
  if cmp -s "$SRC" "$LIVE"; then
    echo "• live style.css is already Aurora — no backup taken (re-apply)."
  else
    cp -p "$LIVE" "$BACKUP"
    echo "✓ backed up existing style → $BACKUP"
  fi
elif [ -e "$BACKUP" ]; then
  echo "• backup already exists ($BACKUP) — left as-is."
else
  echo "• no existing style.css to back up (fresh install)."
fi

# 2. Install our style (plain copy — survives swaync's CSS reload + relogin;
#    a symlink would dangle if this checkout moves, so we copy).
install -m644 "$SRC" "$LIVE"
echo "✓ installed Aurora swaync style → $LIVE"

# 3. Reload ONLY if swaync is already running — never launch it.
if command -v swaync-client >/dev/null 2>&1 && pgrep -x swaync >/dev/null 2>&1; then
  swaync-client --reload-css    >/dev/null 2>&1 || true
  swaync-client --reload-config >/dev/null 2>&1 || true
  echo "✓ swaync is running — reloaded CSS live."
else
  echo "• swaync not running — NOT launching it."
  echo "  (effective on next swaync start / reload)"
fi

echo "✓ Aurora swaync style applied. Revert any time: $HERE/restore.sh"
