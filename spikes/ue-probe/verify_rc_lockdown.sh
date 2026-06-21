#!/usr/bin/env bash
# verify_rc_lockdown.sh — the ADR-0029 §B post-cook RC lockdown gates (turnkey).
#
# Run against a RUNNING UE Remote Control server (a cooked -game build or the editor
# launched with the AgentOSBlank lockdown config + RC started). Proves the §B lockdown:
#   (a) the generic ExecuteConsoleCommand code-exec primitive is REJECTED;
#   (b) a non-allowlisted function (QuitGame) is REJECTED (default-deny allowlist);
#   (c) the ONE allowlisted reactive verb (SetScalarParameterValue) PASSES the allowlist;
#   (d) :30010 is bound to LOOPBACK only;  (e) :30020 (websocket) is NOT listening.
# Exit 0 = all gates GO; exit 1 = any gate NO-GO. Read-only probes (no GPU, no state change).
#
#   RC_HOST=127.0.0.1 RC_PORT=30010 bash spikes/ue-probe/verify_rc_lockdown.sh
set -u

RC_HOST="${RC_HOST:-127.0.0.1}"
RC_PORT="${RC_PORT:-30010}"
WS_PORT="${WS_PORT:-30020}"
URL="http://${RC_HOST}:${RC_PORT}/remote/object/call"
MPC="${MPC:-/Game/AgentOS/Materials/MPC_AgentOS_Reactive.MPC_AgentOS_Reactive}"
KISMET="/Script/Engine.Default__KismetMaterialLibrary"
SYSLIB="/Script/Engine.Default__KismetSystemLibrary"

pass=0; fail=0
ok()   { echo "  ✅ GO   — $1"; pass=$((pass+1)); }
nogo() { echo "  ❌ NO-GO — $1"; fail=$((fail+1)); }

# PUT a body, echo "<http_code>\t<body>".
rc_put() {
  curl -sS -m 5 -o /tmp/rc_body.$$ -w '%{http_code}' -X PUT "$URL" \
    -H 'Content-Type: application/json' -d "$1" 2>/dev/null
  printf '\t'; cat /tmp/rc_body.$$ 2>/dev/null; rm -f /tmp/rc_body.$$
}

echo "== ADR-0029 §B RC lockdown verification @ ${URL} =="

# Reachability first — an absent server is an inconclusive run, not a pass.
if ! curl -sS -m 5 -o /dev/null "http://${RC_HOST}:${RC_PORT}/remote/info" 2>/dev/null \
   && ! curl -sS -m 5 -o /dev/null "$URL" -X PUT -d '{}' 2>/dev/null; then
  echo "  ⚠  RC server not reachable on ${RC_HOST}:${RC_PORT} — launch the cooked -game build"
  echo "     (e.g. -ExecCmds=\"WebControl.StartServer\" -RCWebControlEnable) then re-run."
  exit 2
fi

# (a) ExecuteConsoleCommand MUST be rejected (console-exec disabled / not allowlisted).
echo "-- (a) generic ExecuteConsoleCommand must be REJECTED --"
RESP=$(rc_put "{\"objectPath\":\"${SYSLIB}\",\"functionName\":\"ExecuteConsoleCommand\",\"parameters\":{\"WorldContextObject\":null,\"Command\":\"stat fps\"},\"generateTransaction\":false}")
CODE=${RESP%%$'\t'*}; BODY=${RESP#*$'\t'}
if [ "$CODE" -ge 200 ] 2>/dev/null && [ "$CODE" -lt 300 ] 2>/dev/null; then
  nogo "ExecuteConsoleCommand returned ${CODE} (ACCEPTED!) — console-exec is OPEN. body: ${BODY}"
else
  ok "ExecuteConsoleCommand rejected (${CODE}): ${BODY}"
fi

# (b) A non-allowlisted function (QuitGame) MUST be rejected (default-deny tripwire).
echo "-- (b) non-allowlisted KismetSystemLibrary::QuitGame must be REJECTED --"
RESP=$(rc_put "{\"objectPath\":\"${SYSLIB}\",\"functionName\":\"QuitGame\",\"parameters\":{},\"generateTransaction\":false}")
CODE=${RESP%%$'\t'*}; BODY=${RESP#*$'\t'}
if [ "$CODE" -ge 200 ] 2>/dev/null && [ "$CODE" -lt 300 ] 2>/dev/null; then
  nogo "QuitGame returned ${CODE} (ACCEPTED!) — bAllowAnyRemoteFunctionCall regressed. body: ${BODY}"
else
  ok "QuitGame rejected (${CODE}): ${BODY}"
fi

# (c) The allowlisted SetScalarParameterValue MUST pass the allowlist (NOT the "not allowed" error).
echo "-- (c) allowlisted SetScalarParameterValue must PASS the allowlist --"
RESP=$(rc_put "{\"objectPath\":\"${KISMET}\",\"functionName\":\"SetScalarParameterValue\",\"parameters\":{\"WorldContextObject\":null,\"Collection\":\"${MPC}\",\"ParameterName\":\"Motion\",\"ParameterValue\":1.0},\"generateTransaction\":false}")
CODE=${RESP%%$'\t'*}; BODY=${RESP#*$'\t'}
if printf '%s' "$BODY" | grep -qiE 'not allowed by remote control'; then
  nogo "SetScalarParameterValue hit the ALLOWLIST rejection (${CODE}) — the reactive verb is not allowlisted. body: ${BODY}"
elif [ "$CODE" = "200" ]; then
  ok "SetScalarParameterValue accepted (200) — allowlist + MPC resolve OK"
else
  # Passed the allowlist but failed for another reason (e.g. MPC not authored yet, WorldContextObject:null).
  ok "SetScalarParameterValue passed the allowlist (${CODE}, non-allowlist error) — author the MPC / confirm WorldContextObject [§B VERIFY-LIVE]. body: ${BODY}"
fi

# (d) :30010 loopback-bound only. The 4th ss column is the local address:port.
echo "-- (d) :${RC_PORT} bound to loopback only --"
LADDR=$(ss -ltnH "sport = :${RC_PORT}" 2>/dev/null | awk '{print $4}' | head -1)
if [ -z "$LADDR" ]; then
  nogo "nothing listening on :${RC_PORT} (server down?)"
elif printf '%s' "$LADDR" | grep -qE '^127\.|^\[?::1\]?:'; then
  ok ":${RC_PORT} loopback-bound (${LADDR})"
else
  nogo ":${RC_PORT} bound NON-loopback (${LADDR}) — fix DefaultEngine.ini [HTTPServer.Listeners]"
fi

# (e) :30020 websocket must NOT be listening.
echo "-- (e) websocket :${WS_PORT} must be ABSENT --"
WS=$(ss -ltnH "sport = :${WS_PORT}" 2>/dev/null)
if [ -z "$WS" ]; then
  ok ":${WS_PORT} not listening (websocket server off)"
else
  nogo ":${WS_PORT} IS listening (bAutoStartWebSocketServer should be False): ${WS}"
fi

echo "== §B verdict: ${pass} GO, ${fail} NO-GO =="
[ "$fail" -eq 0 ] && { echo "ALL §B GATES GO"; exit 0; } || { echo "§B NOT SATISFIED"; exit 1; }
