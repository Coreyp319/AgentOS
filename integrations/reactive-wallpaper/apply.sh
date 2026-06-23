#!/usr/bin/env bash
# AgentOS reactive-wallpaper — flip the LIVE com.nimbus.aurora wallpaper to a procedural
# SHADER Style (no external engine) that reacts to the agentosd `feed` producer's agent.json.
# This is GLUE only: the wallpaper plugin (com.nimbus.aurora), its main.qml consumer, the
# shader, and the producer (agentosd `feed` → nimbus-aurora-agent.service, shipped in
# core-substrate) already exist. We only switch the desktop(s) over and remember how to undo it.
#
# Default Style = 1 (Hills), a 2-D scene-graph look. Style < 9 is the contract that NO standalone
# engine launches (per com.nimbus.aurora config/main.xml: 0 Flow, 1 Hills, … 8 Liquid are 2-D;
# 9..14 launch the bevy/UE engine). So this stays a pure shader — zero VRAM-heavy engine, no kill.
#
# Reversibility (ADR-0005 / brief must-fix #10): we capture the PRIOR wallpaperPlugin AND Style in
# a SINGLE evaluateScript readback (no torn-state two-read race), write the prev-state file
# ATOMICALLY (temp + mv, 0600), and are IDEMPOTENT — a pre-existing prev-state file (a prior
# un-restored apply) is never overwritten, so a second apply can't capture Hills(1) as "the prior"
# and corrupt the revert. restore.sh reverses this exactly.
#
# Privacy (must-fix #22): the prev-state file holds EXACTLY the operations/inverse —
# {wallpaperPlugin id, Style int, optional Image path}. No usage timestamps, no reduceMotion /
# AnimationDurationFactor capture (reduceMotion is honored at render time by main.qml, never
# persisted here).
#
# Fail-open: this reacts only when the core-substrate `feed` producer is running; if it is not,
# the wallpaper degrades to a static/idle (or main.qml's "blind") look — apply NEVER errors on a
# missing feed. User-scope, reversible via restore.sh.
#
# Plasma-scripting modeled on the Nimbus pack's
#   ~/whitesur-cachyos-pack/9-gpu-effects/interactive-bg/apply.sh   (wallpaperPlugin swap)
#   ~/whitesur-cachyos-pack/9-gpu-effects/interactive-bg/nimbus-aurora-look.sh (Style read/write in
#                                                       the ['Wallpaper','com.nimbus.aurora','General'] group)
# We inline a minimal evaluateScript because that helper snapshots a different surface (the whole
# "look": Theme/Appearance/Intensity/…), not the single {plugin,Style} op+inverse this revert needs.
set -euo pipefail

PLUGIN_ID="com.nimbus.aurora"
# 1 = Hills (Style < 9 ⇒ no external engine). Force an integer (it is written raw into plasma JS):
# clamp to [0,8] so a stray env value can neither break the JS nor select an engine style (≥9).
STYLE_DEFAULT="$(printf '%d' "${REACTIVE_WALLPAPER_STYLE:-1}" 2>/dev/null || echo 1)"
{ [ "$STYLE_DEFAULT" -ge 0 ] && [ "$STYLE_DEFAULT" -le 8 ]; } 2>/dev/null || STYLE_DEFAULT=1

# Prev-state lives under XDG_STATE_HOME (durable reversibility state, not volatile cache).
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/agentos/reactive-wallpaper"
PREV="$STATE_DIR/prev-wallpaper.json"
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
FEED="$RUNTIME/nimbus-aurora/agent.json"

ok(){   printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$1"; }

# --- 0. plasma tooling guard ---------------------------------------------------------------
# Desktop-tier component: on a headless / non-Plasma host there is no plasmashell to script.
# Skip cleanly (exit 0) rather than failing the whole install (mirrors keyhole/window-drag-wind).
QDBUS=""
for c in qdbus6 qdbus; do command -v "$c" >/dev/null 2>&1 && { QDBUS="$c"; break; }; done
if [ -z "$QDBUS" ]; then
  echo "• reactive-wallpaper: qdbus6 not found (not a Plasma 6 desktop?) — skipping the wallpaper switch"
  exit 0
fi

# --- 1. reach the running user's session bus (hardened; must-fix #17) ----------------------
# Prefer the login/systemd session bus over scraping /proc/<pid>/environ. When run from a Plasma
# session (or the ADR-0043 `systemd-run --user` adopt worker) these are already set and this is a
# no-op. The fallback resolves plasmashell by EXACT comm match AND same-UID ownership — never a
# comm SUBSTRING (no confused-deputy: a process merely *containing* "plasmashell" is rejected) and
# never another user's process (the environ read is gated on uid == our uid).
ensure_session_env() {
  : "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
  # 1a. The standard user bus socket — the canonical address for THIS uid's session.
  if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "$XDG_RUNTIME_DIR/bus" ]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
  fi
  if [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ] && { [ -n "${WAYLAND_DISPLAY:-}" ] || [ -n "${DISPLAY:-}" ]; }; then
    return 0
  fi
  # 1b. Last resort: borrow display vars from OUR plasmashell. EXACT comm + same-uid only.
  local me pid uid comm
  me="$(id -u)"
  for pid in $(pgrep -x plasmashell 2>/dev/null || true); do
    [ -r "/proc/$pid/status" ] || continue
    # same-UID ownership: the real uid (field 1 of the Uid: line) must equal ours.
    uid="$(awk '/^Uid:/{print $2; exit}' "/proc/$pid/status" 2>/dev/null || true)"
    [ "$uid" = "$me" ] || continue
    # exact comm match (defensive — pgrep -x already exacts, but never trust a substring).
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

# --- 2. capture prior state ATOMICALLY + IDEMPOTENTLY (must-fix #10) ------------------------
mkdir -p "$STATE_DIR"
if [ -s "$PREV" ]; then
  # A prior un-restored apply already recorded the genuine prior wallpaper. Do NOT overwrite it
  # (else this apply would capture Hills(1) — the look it is ABOUT to set — as "the prior" and the
  # revert would land you back on the shader, not your real wallpaper). Re-applying just re-flips.
  ok "prev-state already recorded ($PREV) — keeping it (idempotent; not re-capturing)"
else
  # SINGLE evaluateScript readback of BOTH wallpaperPlugin AND its Style (no two-read torn state).
  # If the prior plugin is org.kde.image we also keep its Image path so restore can put the picture
  # back. We emit a single JSON line; a Plasma scripting failure yields empty → we treat the prior
  # as unknown and DO NOT write a bogus prev-state (restore then falls back to a safe default).
  RAW="$(plasma_eval "
    var d = desktops()[0];
    var plug = d.wallpaperPlugin;
    d.currentConfigGroup = ['Wallpaper', plug, 'General'];
    var style = d.readConfig('Style');
    var img   = d.readConfig('Image');
    print(JSON.stringify({plugin: plug, style: style, image: img}));
  " 2>/dev/null || true)"

  # Normalize to the op+inverse shape {plugin, style:int, image?} ONLY (no timestamps, no
  # reduceMotion — #22). Exit non-zero (→ empty) if the prior plugin is unknown, so we never write
  # a bogus prev-state; restore then falls back to a safe default rather than to garbage.
  # NB: the raw JSON is passed as argv[1], NOT on stdin — `python3 - <<'PY'` would redirect stdin to
  # the heredoc and starve a stdin read (the gotcha the pack's nimbus-aurora-look.sh documents).
  PREV_JSON="$(python3 - "$RAW" <<'PY' 2>/dev/null || true
import sys, json
raw = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    o = json.loads(raw)
    plug = o.get("plugin") or ""
except Exception:
    plug = ""
if not plug:
    sys.exit(1)
out = {"plugin": plug}
s = o.get("style")
try:
    out["style"] = int(float(str(s))) if str(s).strip() not in ("", "None") else 0
except Exception:
    out["style"] = 0
img = (o.get("image") or "").strip()
if img:
    out["image"] = img
print(json.dumps(out))
PY
)"

  if [ -n "$PREV_JSON" ]; then
    # Atomic write: temp in the SAME dir (same filesystem ⇒ mv is atomic), 0600, then rename.
    TMP="$(mktemp "$STATE_DIR/.prev.XXXXXX")"
    printf '%s\n' "$PREV_JSON" > "$TMP"
    chmod 600 "$TMP"
    mv -f "$TMP" "$PREV"
    ok "captured prior wallpaper for revert → $PREV"
  else
    warn "could not read the prior wallpaper from plasmashell — restore will fall back to a safe default"
  fi
fi

# --- 3. switch every desktop to the aurora shader at Style $STYLE_DEFAULT (Hills) -----------
# Set both: the plugin id (so the shader plugin renders) and the Style key in its General group.
# Style < 9 guarantees no external engine launches. We do NOT touch reduceMotion / animation
# factors — those are honored live by main.qml, never written here.
if plasma_eval "
  var ds = desktops();
  for (var i = 0; i < ds.length; i++) {
    ds[i].wallpaperPlugin = '$PLUGIN_ID';
    ds[i].currentConfigGroup = ['Wallpaper', '$PLUGIN_ID', 'General'];
    ds[i].writeConfig('Style', $STYLE_DEFAULT);
    ds[i].reloadConfig();
  }
  print('ok');
" >/dev/null 2>&1; then
  ok "reactive shader wallpaper set ($PLUGIN_ID, Style $STYLE_DEFAULT / Hills) on all desktops"
else
  warn "could not switch the wallpaper live — set com.nimbus.aurora (Style $STYLE_DEFAULT) in System Settings → Wallpaper"
fi

# --- 4. gentle producer check (FAIL-OPEN — never error on a missing feed) -------------------
if [ -f "$FEED" ]; then
  ok "feed present ($FEED) — the wallpaper reacts to live agent state"
elif systemctl --user is-enabled nimbus-aurora-agent.service >/dev/null 2>&1; then
  echo "  producer enabled (nimbus-aurora-agent.service); the wallpaper starts reacting once it ticks"
else
  echo "  ! no agent.json feed yet — install the 'core-substrate' component to run the 'feed' producer."
  echo "    Until then the wallpaper renders a calm static look (no error); it reacts once the feed is live."
fi

# --- 5. honesty: the "stale ≠ serene" freshness look (ADR-0030 D9) -------------------------------
# agent.json carries MOOD but not LIVENESS — a dead producer leaves the last mood at rest, byte-
# identical to a genuinely calm fleet. The fix is a heartbeat.json read in the wallpaper's main.qml
# that drops to a distinct quieter-than-idle "blind" look. That edit lives in the Nimbus pack SOURCE
# (~/whitesur-cachyos-pack/9-gpu-effects). We do NOT auto-copy it over your installed plugin, because
# the pack source can also carry your own in-progress Style≥9 (3D/UE) work — yours to ship deliberately,
# not for an adopt to deploy silently. So we only CHECK + tell the truth; we never over-claim.
INST_QML="$HOME/.local/share/plasma/wallpapers/com.nimbus.aurora/contents/ui/main.qml"
if [ -f "$INST_QML" ] && grep -q 'heartbeat' "$INST_QML" 2>/dev/null; then
  ok "installed wallpaper plugin is heartbeat-aware — a stalled feed shows a distinct 'blind' look"
else
  warn "your installed wallpaper plugin predates the stale-feed 'blind' look — until it's updated, the"
  warn "wallpaper reflects MOOD but won't visibly flag a stalled feed. Activate it with a Nimbus pack"
  warn "re-install:  ~/whitesur-cachyos-pack/9-gpu-effects/install.sh   (then re-login)."
fi
echo "  • Revert: $(cd "$(dirname "$0")" && pwd)/restore.sh"
