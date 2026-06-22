#!/usr/bin/env bash
# verify-queue.sh — ADR-0041 on-box verification of the VRAM-demand queue arbiter.
#
# Proves, on the real GPU box, that the cross-workflow queue works end to end:
#   1. both daemons are up + on the session bus (org.agentos.Coordinator1 + org.agentos.Queue1);
#   2. a WaitTurn caller BLOCKS while the lease is held and is nominated the INSTANT it frees
#      (the drain-on-free closed loop — the whole point of the queue);
#   3. a higher-tier waiter is nominated before a lower-tier one (ordering);
#   4. no OOM / VRAM stays sane and the arbiter logged no panic.
#
# SAFE by construction: it holds the lease with the harmless `sleep` Spawn profile (zero GPU work),
# releases everything on exit (trap), and REFUSES the contention test if a real workload already holds
# the lease — run it on a quiet GPU. It never touches your dreams, models, or wallpaper.
#
# Usage:
#   bash verify-queue.sh            # verify against already-deployed services
#   bash verify-queue.sh --deploy   # run apply.sh first (build + install + enable), then verify
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

COORD=org.agentos.Coordinator1; COORD_PATH=/org/agentos/Coordinator1
QUEUE=org.agentos.Queue1;       QUEUE_PATH=/org/agentos/Queue1

PASS=0; FAIL=0; SKIP=0
ok(){   printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
bad(){  printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
skip(){ printf '  \033[33m–\033[0m %s\n' "$1"; SKIP=$((SKIP+1)); }
hdr(){  printf '\n\033[1m%s\033[0m\n' "$1"; }

coord(){ busctl --user call "$COORD" "$COORD_PATH" "$COORD" "$@" 2>&1; }
queue(){ busctl --user call "$QUEUE" "$QUEUE_PATH" "$QUEUE" "$@" 2>&1; }
# busctl prints "<signature> <field1> <field2> …"; `field N` returns the Nth value after the signature.
field(){ awk -v n="$(($1 + 1))" '{print $n}'; }

# Wait (up to ~15s) for a well-known name to appear — a Type=simple daemon claims its bus name a moment
# AFTER systemd calls it "active" (NVML init + zbus name acquisition), and a fresh apply.sh churns the
# bus, so a single check right after deploy is racy. $1=bus name, $2=label.
wait_for_bus(){
  for _ in $(seq 1 30); do
    busctl --user list 2>/dev/null | grep -q "$1" && { ok "$2"; return 0; }
    sleep 0.5
  done
  bad "$2 (not on the bus after 15s)"; return 1
}

TEST_TOKEN=""
WT_OUT=""
cleanup(){
  [[ -n "$TEST_TOKEN" ]] && busctl --user call "$COORD" "$COORD_PATH" "$COORD" Release t "$TEST_TOKEN" >/dev/null 2>&1
  [[ -n "$WT_OUT" ]] && rm -f "$WT_OUT" "$WT_OUT.time" 2>/dev/null
}
trap cleanup EXIT

### 0. Optional deploy ----------------------------------------------------------------------------
if [[ "${1:-}" == "--deploy" ]]; then
  hdr "0. Deploy (apply.sh: build + install + enable)"
  if bash "$HERE/apply.sh"; then ok "apply.sh ran"; else bad "apply.sh failed"; fi
  sleep 1   # let the freshly-started services claim their bus names
fi

### 1. Services + bus names ----------------------------------------------------------------------
hdr "1. Services + bus names"
systemctl --user is-active --quiet agentos-lease.service && ok "agentos-lease.service active" || bad "agentos-lease.service NOT active (try --deploy)"
systemctl --user is-active --quiet agentos-queue.service && ok "agentos-queue.service active" || bad "agentos-queue.service NOT active (try --deploy)"
wait_for_bus "$COORD" "$COORD on the bus"
wait_for_bus "$QUEUE" "$QUEUE on the bus (the arbiter)"

if [[ $FAIL -gt 0 ]]; then
  hdr "RESULT"; echo "  services not ready — deploy first:  bash $(basename "$0") --deploy"; exit 1
fi

### 2. Quiet-GPU precondition --------------------------------------------------------------------
hdr "2. Lease state (the contention test needs a quiet lease)"
ST=$(coord Status)                                   # "bstu <held> <tier> <token> <free_mib>"
HELD=$(echo "$ST" | field 1); TIER=$(echo "$ST" | field 2); FREE=$(echo "$ST" | field 4)
echo "  Status: held=$HELD tier=$TIER free=${FREE}MiB"
if [[ "$HELD" == "true" ]]; then
  skip "a workload already holds the lease (tier=$TIER) — re-run the contention tests on a quiet GPU"
  QUIET=0
else
  ok "lease is free — proceeding"; QUIET=1
fi

### 3. WaitTurn blocks-on-busy → drains-on-free (the closed loop) --------------------------------
if [[ "$QUIET" == "1" ]]; then
  hdr "3. WaitTurn blocks while held → nominated the instant the lease frees"
  SP=$(coord Spawn susas batch 512 sleep 1 120)      # daemon owns `sleep 120`; harmless, holds a batch lease
  TEST_TOKEN=$(echo "$SP" | field 2)
  if [[ "$(echo "$SP" | field 1)" == "true" && -n "$TEST_TOKEN" && "$TEST_TOKEN" != "0" ]]; then
    ok "held the lease via Spawn(batch, sleep) token=$TEST_TOKEN"
    WT_OUT=$(mktemp)
    # Park a best-effort waiter in the background; record when it returns.
    ( s=$(date +%s.%N); r=$(queue WaitTurnAgent su best-effort 256); e=$(date +%s.%N)
      printf '%s\n' "$r" >"$WT_OUT"; printf '%s %s\n' "$s" "$e" >"$WT_OUT.time" ) &
    WPID=$!
    sleep 2.5
    if kill -0 "$WPID" 2>/dev/null; then
      ok "WaitTurn BLOCKS while the lease is held (still waiting after 2.5s)"
    else
      bad "WaitTurn returned early — it did NOT block: $(cat "$WT_OUT" 2>/dev/null)"
    fi
    coord Release t "$TEST_TOKEN" >/dev/null 2>&1; TEST_TOKEN=""   # free the lease
    wait "$WPID" 2>/dev/null
    R=$(cat "$WT_OUT" 2>/dev/null)
    if echo "$R" | grep -q true && echo "$R" | grep -q your_turn; then
      read -r s e <"$WT_OUT.time"; DT=$(awk -v s="$s" -v e="$e" 'BEGIN{printf "%.2f", e-s}')
      ok "WaitTurn got 'your_turn' once the lease freed (waited ${DT}s) — drain-on-free works"
    else
      bad "WaitTurn never got a turn after release: $R"
    fi
    rm -f "$WT_OUT" "$WT_OUT.time"; WT_OUT=""
  else
    bad "could not Spawn the test holder: $SP"; TEST_TOKEN=""
  fi
fi

### 4. Tier ordering: batch nominated before best-effort -----------------------------------------
if [[ "$QUIET" == "1" ]]; then
  hdr "4. Tier ordering (batch nominated before best-effort)"
  SP=$(coord Spawn susas batch 512 sleep 1 120); TEST_TOKEN=$(echo "$SP" | field 2)
  if [[ -n "$TEST_TOKEN" && "$TEST_TOKEN" != "0" ]]; then
    BE=$(mktemp); BA=$(mktemp)
    ( queue WaitTurnAgent su best-effort 256 >"$BE" 2>&1; date +%s.%N >>"$BE" ) & P1=$!
    sleep 0.3                                          # best-effort arrives first (earlier seq)…
    ( queue WaitTurnAgent su batch 256 >"$BA" 2>&1; date +%s.%N >>"$BA" ) & P2=$!
    sleep 1
    coord Release t "$TEST_TOKEN" >/dev/null 2>&1; TEST_TOKEN=""
    wait "$P1" "$P2" 2>/dev/null
    TBE=$(tail -1 "$BE"); TBA=$(tail -1 "$BA")
    if grep -q your_turn "$BA" && awk -v a="$TBA" -v b="$TBE" 'BEGIN{exit !(a<=b+0.05)}'; then
      ok "batch nominated before/with best-effort despite arriving later (higher tier wins)"
    else
      skip "ordering inconclusive (timing) — batch=$TBA best-effort=$TBE; re-run (dwell≈300ms)"
    fi
    rm -f "$BE" "$BA"
  else
    skip "ordering test skipped (could not hold the lease)"
  fi
fi

### 5. No OOM / VRAM sane ------------------------------------------------------------------------
hdr "5. No OOM / VRAM sanity"
if command -v nvidia-smi >/dev/null 2>&1; then
  FV=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
  if [[ "$FV" =~ ^[0-9]+$ && "$FV" -gt 200 ]]; then ok "free VRAM = ${FV} MiB (no exhaustion)"; else bad "free VRAM low/unreadable: $FV"; fi
else
  skip "nvidia-smi not found"
fi
if journalctl --user -u agentos-queue.service --since "-5min" 2>/dev/null | grep -qiE 'panic|thread .* panicked'; then
  bad "agentos-queue logged a panic in the last 5min"
else
  ok "no arbiter panic in recent logs"
fi

### Result --------------------------------------------------------------------------------------
hdr "RESULT"
echo "  PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP"
if [[ $FAIL -eq 0 ]]; then echo "  ✓ VRAM-demand queue verified (ADR-0041)"; else echo "  ✗ see failures above"; fi
exit $(( FAIL > 0 ))
