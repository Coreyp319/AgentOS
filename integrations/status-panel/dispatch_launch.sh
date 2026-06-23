#!/usr/bin/env bash
# AgentOS — KRunner-reachable "dispatch a local Hermes agent to fix a down service" (ADR-0039/0031).
#
# The SMALL reuse path: a .desktop launcher (KRunner-indexed) that POSTs the EXISTING /dispatch route
# on the status panel. It is deliberately constrained:
#   • HERMES-ONLY (local). It sends source=launcher, and the panel FORCES target=hermes and refuses an
#     explicit cloud (claude) dispatch at the server boundary — KRunner has no browser consent surface,
#     so a one-keystroke cloud dispatch is impossible from here (enforced at the route, not this script).
#   • CONSENT-GATED. Nothing is dispatched without a notify-send confirm naming the resolved service.
#   • NO AUTO-PICK. When >1 service needs attention we route to the panel rather than guess.
# It reuses the whole ADR-0039 server-side safety spine (redaction, rate-limit, crashloop guard,
# kill-switch). Loopback only. Invoked as `bash dispatch_launch.sh` (the file stays 0644).
set -euo pipefail

PANEL="http://127.0.0.1:${AGENTOS_STATUS_PORT:-9123}"
APP="AgentOS dispatch"

notify() { command -v notify-send >/dev/null 2>&1 && notify-send -a "$APP" "$@" >/dev/null 2>&1 || true; }
open_panel() { command -v xdg-open >/dev/null 2>&1 && xdg-open "$PANEL/atrium" >/dev/null 2>&1 || true; }

# 1. Which services need attention? Replicate the panel's _is_attention over /status.json (read-only).
STATUS="$(curl -fsS --max-time 4 "$PANEL/status.json" 2>/dev/null || true)"
if [ -z "$STATUS" ]; then
  notify "AgentOS panel unreachable" "Is the status panel (:9123) running?"
  exit 0
fi

mapfile -t DOWN < <(printf '%s' "$STATUS" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for s in d.get("services", []):
    st = s.get("status"); kind = s.get("kind", "daemon"); reach = s.get("reach")
    att = (st == "failed") or (st == "down" and kind == "daemon") or (st in ("up", "starting") and reach == "unreachable")
    if att and s.get("id"):
        print(s["id"])
' 2>/dev/null || true)

if [ "${#DOWN[@]}" -eq 0 ]; then
  notify "Nothing needs dispatching" "All AgentOS services look healthy."
  exit 0
fi
if [ "${#DOWN[@]}" -gt 1 ]; then
  notify "${#DOWN[@]} services need attention" "Open the status panel to choose which to dispatch."
  open_panel
  exit 0
fi

SVC="${DOWN[0]}"
# id must be a clean catalog id (defense in depth — the POST body is built with json.dumps, never
# shell interpolation, so target can't be overridden; this just rejects a surprising status.json).
case "$SVC" in *[!a-z0-9-]*|"") notify "Dispatch aborted" "unexpected service id"; exit 1 ;; esac

# 2. Per-invocation CONSENT — a confirm naming the resolved service (this notify action IS the consent,
# since KRunner has no browser prompt). Needs libnotify 0.8+ (-A/--wait). If actions aren't supported,
# we open the panel rather than dispatch unconfirmed.
if notify-send --help 2>&1 | grep -q -- '--action'; then
  ACT="$(notify-send -a "$APP" -A go="Dispatch Hermes" -w \
         "Dispatch a local Hermes agent?" \
         "\"$SVC\" needs attention — investigate & fix it locally? Nothing leaves your box." 2>/dev/null || true)"
  [ "$ACT" = "go" ] || exit 0
else
  notify "Open the panel to dispatch" "\"$SVC\" needs attention — your notifier can't confirm inline."
  open_panel
  exit 0
fi

# 3. POST the existing /dispatch route — target=hermes + source=launcher. The body is built with
# json.dumps so `target` is structurally un-overridable; the panel forces local for source=launcher.
TOKEN="$(curl -fsS --max-time 4 "$PANEL/dispatch/token" 2>/dev/null \
         | python3 -c 'import sys,json; print(json.load(sys.stdin).get("token",""))' 2>/dev/null || true)"
[ -n "$TOKEN" ] || { notify "Dispatch failed" "couldn't get a panel token"; exit 1; }
BODY="$(python3 -c 'import json,sys; print(json.dumps({"id": sys.argv[1], "target": "hermes", "source": "launcher"}))' "$SVC")"
RESP="$(curl -fsS --max-time 8 -X POST "$PANEL/dispatch" \
        -H "Content-Type: application/json" -H "X-Dispatch-Token: $TOKEN" \
        --data "$BODY" 2>/dev/null || true)"
if printf '%s' "$RESP" | grep -q '"status"'; then
  notify "Dispatched Hermes" "Investigating \"$SVC\" locally."
else
  MSG="$(printf '%s' "$RESP" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("error","see the status panel"))
except Exception: print("see the status panel")' 2>/dev/null || echo "see the status panel")"
  notify "Dispatch not started" "$MSG"
fi
