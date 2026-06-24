#!/usr/bin/env bash
# AgentOS reactive-wallpaper — reverse apply.sh: switch the desktop(s) back to EXACTLY the
# wallpaperPlugin + Style that were live before apply.sh ran, then remove the prev-state file.
#
# Reads the op+inverse prev-state file {plugin, style, optional image} written atomically by
# apply.sh. On a successful restore it `rm`s that file (ADR-0005: the reversal is consumed).
#
# Absent-file fallback (brief must-fix #10 — never a silent no-op): if the prev-state file is
# missing at restore time (a stale/cleared run, or apply couldn't read the prior wallpaper), we
# fall back to a KNOWN-SAFE default plugin (org.kde.image) AND print loudly that the prior
# wallpaper couldn't be found — so the user is never left silently on the shader, and never lands
# in a third novel state without being told.
#
# Plasma-scripting modeled on the Nimbus pack's interactive-bg/restore.sh (wallpaperPlugin swap +
# Image write-back) and nimbus-aurora-look.sh (Style write in the com.nimbus.aurora General group).
set -euo pipefail

PLUGIN_ID="com.nimbus.aurora"           # the plugin apply.sh switched TO (only restore if it's live)
SAFE_DEFAULT="org.kde.image"            # absent-file fallback plugin (Plasma's built-in picture wallpaper)
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/agentos/reactive-wallpaper"
PREV="$STATE_DIR/prev-wallpaper.json"

ok(){   printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$1"; }

# window-drag → wind is folded into this one toggle, so reverse it too — FIRST, before the
# wallpaper logic's early-exit paths, so the KWin producer is always removed. Fail-soft.
WIND_RESTORE="$(cd "$(dirname "$0")" && pwd)/../window-drag-wind/restore.sh"
if [ -x "$WIND_RESTORE" ]; then
  echo "• window-drag wind:"
  "$WIND_RESTORE" 2>&1 | sed 's/^/  /' || warn "window-drag wind removal hit an issue"
fi

QDBUS=""
for c in qdbus6 qdbus; do command -v "$c" >/dev/null 2>&1 && { QDBUS="$c"; break; }; done
if [ -z "$QDBUS" ]; then
  echo "• reactive-wallpaper: qdbus6 not found (not a Plasma 6 desktop?) — nothing to revert."
  rm -f "$PREV" 2>/dev/null || true
  exit 0
fi

# Reach the running user's session bus, hardened the same way apply.sh does: prefer the login/
# systemd user bus, fall back to OUR plasmashell by EXACT comm + same-UID only (no confused deputy).
ensure_session_env() {
  : "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
  if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "$XDG_RUNTIME_DIR/bus" ]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
  fi
  if [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ] && { [ -n "${WAYLAND_DISPLAY:-}" ] || [ -n "${DISPLAY:-}" ]; }; then
    return 0
  fi
  local me pid uid comm
  me="$(id -u)"
  for pid in $(pgrep -x plasmashell 2>/dev/null || true); do
    [ -r "/proc/$pid/status" ] || continue
    uid="$(awk '/^Uid:/{print $2; exit}' "/proc/$pid/status" 2>/dev/null || true)"
    [ "$uid" = "$me" ] || continue
    comm="$(tr -d '\0' < "/proc/$pid/comm" 2>/dev/null || true)"
    [ "$comm" = "plasmashell" ] || continue
    [ -r "/proc/$pid/environ" ] || continue
    while IFS= read -r -d '' line; do
      case "$line" in
        WAYLAND_DISPLAY=*|XDG_RUNTIME_DIR=*|DBUS_SESSION_BUS_ADDRESS=*|DISPLAY=*) export "$line" ;;
      esac
    done < "/proc/$pid/environ"
    break
  done
}
ensure_session_env

plasma_eval() { "$QDBUS" org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript "$1"; }

# Only act if our shader is actually the live wallpaper — if the user has since moved on to some
# other wallpaper themselves, do not yank it out from under them.
CUR="$(plasma_eval 'print(desktops()[0].wallpaperPlugin)' 2>/dev/null || true)"
if [ "$CUR" != "$PLUGIN_ID" ]; then
  ok "the reactive shader isn't the active wallpaper (now: ${CUR:-unknown}) — left wallpaper as-is."
  rm -f "$PREV" 2>/dev/null || true   # our reversal is moot; drop the stale record
  exit 0
fi

# --- resolve the target {plugin, style, image} ---------------------------------------------
if [ -s "$PREV" ]; then
  # Parse the op+inverse record into discrete fields (one python call, each field on its own line
  # so a path with spaces survives). Defaults are inert (org.kde.image / style 0 / no image) so a
  # malformed record degrades to the safe default rather than erroring.
  TGT_PLUGIN="$(python3 - "$PREV" <<'PY'
import sys, json
try: o = json.load(open(sys.argv[1]))
except Exception: o = {}
print((o.get("plugin") or "org.kde.image").strip() or "org.kde.image")
PY
)"
  TGT_STYLE="$(python3 - "$PREV" <<'PY'
import sys, json
try: o = json.load(open(sys.argv[1]))
except Exception: o = {}
try: print(int(o.get("style", 0)))
except Exception: print(0)
PY
)"
  TGT_IMAGE="$(python3 - "$PREV" <<'PY'
import sys, json
try: o = json.load(open(sys.argv[1]))
except Exception: o = {}
print((o.get("image") or "").strip())
PY
)"
  : "${TGT_PLUGIN:=$SAFE_DEFAULT}"
  : "${TGT_STYLE:=0}"
  FROM_FILE=1
else
  # Absent-file fallback — NEVER a silent no-op.
  TGT_PLUGIN="$SAFE_DEFAULT"; TGT_STYLE=0; TGT_IMAGE=""
  FROM_FILE=0
  warn "no saved prior wallpaper at $PREV — falling back to the safe default ($SAFE_DEFAULT)."
  warn "(your exact previous wallpaper could not be found; set your preferred one in System Settings → Wallpaper.)"
fi

# --- apply the inverse: set plugin (+ Style for the shader, + Image for org.kde.image) ------
# JSON-encode the captured strings into valid JS string literals so a prior-wallpaper path with an
# apostrophe, backslash, or unicode (e.g. /home/u/Tom's pics/bg.png — a common, legitimate path) can
# NEVER break the evaluateScript source. Raw interpolation would close the JS string early, fail the
# whole revert, and silently strand the user on the shader/default — an irreversibility (ADR-0005).
js_str() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }
PLUG_JS="$(js_str "$TGT_PLUGIN")"
IMG_JS="$(js_str "$TGT_IMAGE")"
if plasma_eval "
  var ds = desktops();
  var plug = $PLUG_JS;
  var img = $IMG_JS;
  for (var i = 0; i < ds.length; i++) {
    ds[i].wallpaperPlugin = plug;
    ds[i].currentConfigGroup = ['Wallpaper', plug, 'General'];
    if (plug === '$PLUGIN_ID') {
      ds[i].writeConfig('Style', $TGT_STYLE);
    } else if (plug === 'org.kde.image' && img.length > 0) {
      ds[i].writeConfig('Image', img);
      ds[i].writeConfig('PreviewImage', img);
    }
    ds[i].reloadConfig();
  }
  print('ok');
" >/dev/null 2>&1; then
  if [ "$TGT_PLUGIN" = "$PLUGIN_ID" ]; then
    ok "wallpaper restored → $TGT_PLUGIN (Style $TGT_STYLE)"
  else
    ok "wallpaper restored → $TGT_PLUGIN"
  fi
  # Consume the reversal only on a successful, file-backed restore. (On the absent-file fallback
  # there is nothing to delete; on failure we keep the file so a retry can still revert.)
  [ "${FROM_FILE:-0}" = "1" ] && rm -f "$PREV"
else
  warn "could not switch the wallpaper live — set it in System Settings → Wallpaper (prev-state kept for retry)."
fi
