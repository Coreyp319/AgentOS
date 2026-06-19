#!/usr/bin/env bash
# ADR-0022 Phase 1 — end-to-end test of the lane scope/cgroup reclaim.
#
# Drives the FULL path: AdoptScope (lane registers its scope) → a higher tier preempts → the daemon
# cgroup.kills the lane's scope → auto-release. The "lane" is a DISPOSABLE systemd scope named with the
# Blender lane allowlist prefix but wrapping a harmless `sleep` — it is NEVER a real authoring lane.
#
# Isolation (so this never disturbs the user's LIVE coordinator/keyhole):
#   * the freshly-built daemon runs on a PRIVATE D-Bus (`dbus-run-session`), not the session bus the
#     installed `agentosd lease` owns;
#   * the daemon's keyhole mirror is redirected to a throwaway XDG_RUNTIME_DIR.
# The throwaway scope is created on the real user manager (it has to be — that's where scopes live) but
# it is sleep-only, uniquely named, and torn down on exit. Only THIS scope's pid is ever passed to the
# daemon, so a real Blender lane running in parallel is never referenced or touched.
#
# NB: cgroup.procs is a kernel pseudo-file that reports st_size==0 even when populated — `[ -s ]` is
# ALWAYS false on it, so emptiness is tested by READING it (`cat`/`head`), never by `-s`.
#
# Run:  cargo build -p agentosd && integrations/blender/test-scope-reclaim.sh
set -euo pipefail

BIN="${AGENTOSD:-target/debug/agentosd}"
[ -x "$BIN" ] || { echo "build first: cargo build -p agentosd  (looked for $BIN)"; exit 1; }
command -v dbus-run-session >/dev/null || { echo "need dbus-run-session"; exit 1; }
BIN="$(realpath "$BIN")"

TMP="$(mktemp -d)"
UNIT="app-flatpak-org.blender.Blender-e2e$$.scope"

cleanup() {
  # Kill the freshly-built daemon (its PID was recorded inside the private-bus shell) BEFORE removing TMP.
  [ -f "$TMP/daemon.pid" ] && kill "$(cat "$TMP/daemon.pid")" 2>/dev/null || true
  systemctl --user stop "$UNIT" 2>/dev/null || true
  systemctl --user reset-failed "$UNIT" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

echo "### 1. create a throwaway Blender-named scope wrapping \`sleep\` (NOT a real lane)"
systemd-run --user --scope --collect --unit="$UNIT" --quiet sleep 600 &
# Resolve the scope's cgroup path (systemctl show is reliable for a single read; retry until present).
CG=""
for _ in $(seq 1 50); do
  CG="$(systemctl --user show "$UNIT" -p ControlGroup --value 2>/dev/null || true)"
  [ -n "$CG" ] && [ "$CG" != "/" ] && break
  CG=""
  sleep 0.1
done
# HARD-FAIL unless the resolved path is actually OUR scope — guards against an empty/“/” ControlGroup
# silently collapsing to the ROOT cgroup (whose cgroup.procs is always populated).
case "$CG" in
  */"$UNIT") : ;;
  *) echo "FAIL: could not resolve a cgroup for $UNIT (got CG='$CG')"; exit 1 ;;
esac
# Resolve the lane pid by READING cgroup.procs (content check — NOT `[ -s ]`, which is always false here).
LANE_PID=""
for _ in $(seq 1 30); do
  LANE_PID="$(head -n1 "/sys/fs/cgroup${CG}/cgroup.procs" 2>/dev/null || true)"
  [ -n "$LANE_PID" ] && break
  sleep 0.1
done
[ -n "$LANE_PID" ] || { echo "FAIL: could not resolve the throwaway lane pid under $CG"; exit 1; }
echo "    lane = $UNIT  pid = $LANE_PID  cgroup = $CG"

echo "### 2. run the freshly-built daemon on a PRIVATE bus + isolated runtime dir, then drive it"
# Quoted heredoc — nothing expands at write time; the inner shell takes TMP/BIN/LANE_PID as args.
cat >"$TMP/inner.sh" <<'INNER'
set -uo pipefail   # NOT -e: we assert outcomes explicitly and always surface the daemon log
TMP="$1"; BIN="$2"; LANE_PID="$3"
mkdir -p "$TMP/xdg"
XDG_RUNTIME_DIR="$TMP/xdg" "$BIN" lease >"$TMP/daemon.log" 2>&1 &
echo $! > "$TMP/daemon.pid"
up=0
for _ in $(seq 1 50); do
  busctl --user list 2>/dev/null | grep -q org.agentos.Coordinator1 && { up=1; break; }
  sleep 0.1
done
[ "$up" = 1 ] || { echo "FAIL: daemon never bound the private bus"; cat "$TMP/daemon.log"; exit 1; }
C="busctl --user -- call org.agentos.Coordinator1 /org/agentos/Coordinator1 org.agentos.Coordinator1"
echo "--- AdoptScope batch est=3000 pid=$LANE_PID (expect granted=true, token>0) ---"
$C AdoptScope suu batch 3000 "$LANE_PID" | tee "$TMP/adopt.out"
grep -q ' true ' "$TMP/adopt.out" || { echo "FAIL: AdoptScope did not return granted=true"; exit 2; }
echo "--- Status (expect held=true tier=batch) ---"
$C Status
echo "--- Acquire interactive (preempts the lane → daemon cgroup.kills its scope) ---"
$C Acquire su interactive 5000 | tee "$TMP/acq.out"
grep -q ' true ' "$TMP/acq.out" || { echo "FAIL: interactive Acquire was not granted"; exit 3; }
echo "--- daemon log ---"
cat "$TMP/daemon.log"
INNER
dbus-run-session -- bash "$TMP/inner.sh" "$TMP" "$BIN" "$LANE_PID" \
  || { echo "FAIL: daemon-side AdoptScope/preempt sequence errored (see above)"; exit 1; }

echo "### 3. assert the throwaway lane was reclaimed (its scope emptied)"
# The reclaim + backpressure already ran inside the interactive Acquire; poll briefly as belt-and-suspenders.
# Emptiness = cgroup gone OR cgroup.procs has no content (read it — `-s` is always false on this pseudo-file).
reclaimed=0
for _ in $(seq 1 30); do
  if [ ! -d "/sys/fs/cgroup${CG}" ] || [ -z "$(cat "/sys/fs/cgroup${CG}/cgroup.procs" 2>/dev/null)" ]; then
    reclaimed=1; break
  fi
  sleep 0.1
done
if [ "$reclaimed" != 1 ]; then
  echo "FAIL: lane scope still has procs — reclaim did NOT fire:"
  cat "/sys/fs/cgroup${CG}/cgroup.procs" 2>/dev/null || true
  exit 1
fi
if kill -0 "$LANE_PID" 2>/dev/null; then
  echo "FAIL: lane pid $LANE_PID still alive"; exit 1
fi
echo "PASS: AdoptScope → interactive preempt → cgroup.kill reclaimed the lane scope"
