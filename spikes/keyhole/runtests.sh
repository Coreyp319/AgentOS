#!/usr/bin/env bash
# runtests.sh — orchestrate the exit-code-gated file-poll proof.
#
# Writes "working" to the target file, launches contracttest.qml (which starts
# polling), then ~2.5s later atomically rewrites the file to "unknown". The QML
# exit code tells us whether the Timer-poll picked up the LIVE change.
#
# This is the de-risking evidence for the dominant unknown: that a Timer-poll
# (fresh XHR each tick) sees on-disk changes, where a one-shot sync XHR would not.
#
# SPDX-License-Identifier: MIT
set -uo pipefail
# The standalone harness reads keyhole.json via XMLHttpRequest on file:// (the
# plasmoid uses the Plasma5Support `cat` DataSource instead). qml6 disables file://
# XHR by default unless this override is set — without it contracttest.qml never
# reaches the file and the suite FAILs with exit 10 (a harness-only env gotcha, not
# a regression in the plasmoid, which has no XHR path). Export it so the suite is
# robust out of the box.
export QML_XHR_ALLOW_FILE_READ=1
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF="/tmp/keyhole-contract.json"

write_atomic() { local b="$1"; local tmp="/tmp/.keyhole-contract.$$.tmp"; printf '%s\n' "$b" > "$tmp"; mv -f "$tmp" "$TF"; }

# phase 1: working
write_atomic '{ "schema":1, "state":"working", "gateway":"running",
  "floats":{"busy":0.85,"warm":0.0,"snag":0.0},
  "fleet":{"running":3,"queued":2,"snagged":0},
  "lease":{"tier":"interactive","holder":"Hermes","preempt":"wallpaper yielded ~1.5GB"},
  "vram":{"used_mib":6240,"total_mib":8192},
  "residency":[{"name":"qwen2.5:14b","loaded_secs":240}],
  "tokens_per_sec":null }'

echo "[test] phase1=working written; launching QML poller…"
# launch the poller in the background; rewrite the file mid-flight
( sleep 2.5
  echo "[test] phase2: rewriting file -> unknown (gateway unreachable)"
  write_atomic '{ "schema":1, "state":"unknown", "gateway":"unknown",
    "floats":{"busy":0.0,"warm":0.0,"snag":0.0},
    "fleet":{"running":-1,"queued":-1,"snagged":-1},
    "lease":{"tier":"","holder":"","preempt":""},
    "vram":{"used_mib":-1,"total_mib":-1},
    "residency":[], "tokens_per_sec":null }'
) &
REWRITER=$!

timeout 30 qml6 "$DIR/contracttest.qml" -- "$TF"
CODE=$?
wait $REWRITER 2>/dev/null

echo "[test] QML exit code = $CODE"
case "$CODE" in
  0)  echo "[test] PASS — live update observed (working→unknown) AND UNKNOWN honesty (em-dash tok/s + vram) held." ;;
  10) echo "[test] FAIL — never reached the file." ;;
  20) echo "[test] FAIL — phase-1 'working' never observed." ;;
  30) echo "[test] FAIL — STALE: file rewritten but poll did NOT pick up the change (the sync-XHR failure mode)." ;;
  40) echo "[test] FAIL — change picked up but UNKNOWN broke honesty (tok/s or vram not em-dash)." ;;
  124) echo "[test] FAIL — timed out (QML never exited)." ;;
  *)  echo "[test] FAIL — unexpected exit $CODE." ;;
esac
exit $CODE
