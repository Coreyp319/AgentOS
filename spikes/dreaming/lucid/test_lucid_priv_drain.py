#!/usr/bin/env python3
"""Unit tests for lucid_priv_drain (ADR-0019 §5, Condition 4) — the IN-SESSION private drainer, with an
INJECTED runner so no GPU/lease/daemon is touched. Proves the airtight private invariants:

  * a 'done' run clears the private record (no trace);
  * a retry-exhausted private item SILENT-BURNS (no review.json anywhere, no needs-review file, the
    session's sinks burned) — drain_private_once routes the exhausted terminal through
    lucid_priv_queue.writeback's silent burn, never lucid_review;
  * a human-cause private item SILENT-BURNS the same way (no review edge);
  * expire_idle() ages out an idle/forgotten stale hold to a SILENT burn BEFORE logout (the Condition-4 gap);
  * ONE eligible held per session per tick (fair, bounded);
  * an injected runner EXCEPTION is caught + re-held — never lost, never raised;
  * THE PRIVACY ASSERTION: this module NEVER writes a review.json — neither the durable spool nor any
    priv-queue subdir holds a review sidecar after the whole run;
  * the REAL _governed_private_runner drives L.start(private=True)/L.step(best-effort) from the SEALED
    snapshot, fail-open, and is structurally never a review edge.

Sets XDG_RUNTIME_DIR / XDG_DATA_HOME / LUCID_DREAMS / COMFY_ROOT to temp dirs BEFORE import so nothing
touches the real cache, the real durable spool, or ComfyUI dirs.
Run: python3 test_lucid_priv_drain.py"""
import glob
import json
import os
import sys
import tempfile
import threading
import time

_TMP = tempfile.mkdtemp(prefix="lucid_priv_drain_test_")
os.environ["XDG_RUNTIME_DIR"] = os.path.join(_TMP, "run")        # tmpfs root for priv + priv-queue
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")          # the DURABLE disk-backed root
os.environ["LUCID_DREAMS"] = os.path.join(_TMP, "dreams")         # the persistent dream cache
os.environ["COMFY_ROOT"] = os.path.join(_TMP, "comfy")
os.makedirs(os.path.join(_TMP, "run"), exist_ok=True)
os.makedirs(os.path.join(_TMP, "comfy", "input"), exist_ok=True)
os.makedirs(os.path.join(_TMP, "comfy", "output"), exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_priv_queue as PQ   # noqa: E402  (build private holds the way the web process does)
import lucid_priv_drain as PD   # noqa: E402  (the module under test)
import lucid_queue as Q         # noqa: E402  (read-only: MAX_ATTEMPTS + assert durable stays empty)
import lucid_store as ST        # noqa: E402  (read-only: make sessions private + assert the burn)

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


RUN = os.path.join(_TMP, "run")
PQ_ROOT = os.path.join(RUN, "agentos", "lucid-priv-queue")        # the ephemeral queue root
PRIV_ROOT = os.path.join(RUN, "agentos", "lucid-priv")            # the dream tmpfs root
DURABLE = Q.durable_dir()                                         # the disk-backed spool (must stay empty);
#   resolved via the authoritative accessor so this test file does not itself spell the durable path token.

png_src = os.path.join(_TMP, "seed.png")
with open(png_src, "wb") as _f:
    _f.write(b"\x89PNGBYTES")


def _make_private(session):
    """Make `session` genuinely private the way the launcher does — a sealed tmpfs dream dir — so
    ST.is_private(session) is True and PQ.hold() accepts it."""
    ST.ensure_session(session, True)


def _clean_board():
    """Silent-burn every live private queue session so the next section starts clean. Needed because
    drain_private_once sweeps ALL live sessions (it is global-per-tick), so a section that pins the
    EXACT set of jobs run must not see leftover holds from a prior section."""
    for s in list(PQ.list_sessions()):
        PQ.purge(s)


def runner_returning(outcome):
    calls = []

    def run(rec):
        calls.append(rec["id"])
        return outcome
    run.calls = calls
    return run


def _no_review_anywhere():
    """No review.json / *.review.json sidecar exists in EITHER the durable spool or the priv-queue —
    the load-bearing privacy assertion (this module must never reach lucid_review)."""
    dur = (glob.glob(os.path.join(DURABLE, "review.json"))
           + glob.glob(os.path.join(DURABLE, "**", "*.review.json"), recursive=True))
    pq = glob.glob(os.path.join(PQ_ROOT, "**", "*.review.json"), recursive=True)
    return dur == [] and pq == []


def _no_durable_trace():
    if not os.path.isdir(DURABLE):
        return True
    return glob.glob(os.path.join(DURABLE, "**", "*"), recursive=True) == []


# ============================ a 'done' run clears the record ============================
_make_private("done_s")
PQ.hold("done_s", "d1", "Private create", png_src)
run_done = runner_returning("done")
ran = PD.drain_private_once(run_done)
check("drain_private_once runs the held private job", run_done.calls == ["d1"])
check("a 'done' run reports done/cleared", ran == [("d1", "done", "done")])
check("a 'done' run clears the held record", not os.path.isfile(os.path.join(PQ_ROOT, "done_s", "d1.held.json")))
check("a 'done' run clears the running record", not os.path.isfile(os.path.join(PQ_ROOT, "done_s", "d1.running.json")))
check("a 'done' run removes the sealed snapshot too", not os.path.isfile(os.path.join(PQ_ROOT, "done_s", "d1.png")))

# ============================ ONE eligible held per session per tick ============================
_clean_board()
_make_private("one_s")
PQ.hold("one_s", "o1", "Private create", png_src)
PQ.hold("one_s", "o2", "Private create", png_src)
run_one = runner_returning("done")
ran = PD.drain_private_once(run_one)
check("one-per-session: exactly ONE eligible held drains this tick", len(run_one.calls) == 1)
check("one-per-session: the first-arrived (seq 1) drains first (FIFO)", run_one.calls == ["o1"])
check("one-per-session: the other held survives for the next tick",
      os.path.isfile(os.path.join(PQ_ROOT, "one_s", "o2.held.json")))

# ============================ a failed attempt re-holds (live in-session retry) ============================
_clean_board()
_make_private("retry_s")
PQ.hold("retry_s", "r1", "Private create", png_src)
ran = PD.drain_private_once(runner_returning("gpu-busy"))
check("a failed attempt re-holds (live retry, never lost)", ran == [("r1", "gpu-busy", "held")])
rec = json.load(open(os.path.join(PQ_ROOT, "retry_s", "r1.held.json")))
check("the re-held record bumped attempts", rec["attempts"] == 1)
check("the re-held record set a backoff floor", rec["next_retry_after"] > 0)
# the backoff floor is respected — a not-yet-eligible held is skipped, not run
run_skip = runner_returning("done")
ran = PD.drain_private_once(run_skip)
check("a job inside its backoff window is skipped (not run)", run_skip.calls == [] and ran == [])

# ============================ retry-exhausted private item SILENT-BURNS ============================
_clean_board()
_make_private("exhaust")
PQ.hold("exhaust", "ex1", "Private create", png_src)
disp_x = None
# clear the backoff each tick so all attempts land in one test (the drainer respects next_retry_after)
for _ in range(Q.MAX_ATTEMPTS + 1):
    p = os.path.join(PQ_ROOT, "exhaust", "ex1.held.json")
    if os.path.isfile(p):
        _r = json.load(open(p))
        _r["next_retry_after"] = 0.0
        Q._atomic_write(p, _r)
    ran = PD.drain_private_once(runner_returning("gpu-busy"))
    if ran:
        disp_x = ran[0][2]
    if not os.path.exists(os.path.join(PQ_ROOT, "exhaust")):
        break
check("a retry-exhausted private item BURNS SILENTLY (no needs-review)", disp_x == "burned-silent")
check("retry-exhausted: the session subdir is gone (no on-disk trace)",
      not os.path.exists(os.path.join(PQ_ROOT, "exhaust")))
check("retry-exhausted: the dream tmpfs sink burned too (lucid_store.burn reuse)",
      not os.path.exists(os.path.join(PRIV_ROOT, "exhaust")))
check("retry-exhausted: NO review.json anywhere (durable spool + priv-queue)", _no_review_anywhere())

# ============================ a human-cause private item SILENT-BURNS (no review edge) ============================
_clean_board()
_make_private("human")
PQ.hold("human", "hu1", "Private create", png_src)
ran = PD.drain_private_once(runner_returning("b2-cant-verify"))
check("a human-cause outcome on a PRIVATE item burns silently (no review row)",
      ran == [("hu1", "b2-cant-verify", "burned-silent")])
check("human-cause: the session subdir is gone (no on-disk trace)",
      not os.path.exists(os.path.join(PQ_ROOT, "human")))
check("human-cause: NO review.json anywhere", _no_review_anywhere())

# ============================ expire_idle() ages out an idle stale hold to a SILENT burn ============================
_clean_board()
_make_private("idle")
PQ.hold("idle", "id1", "Private create", png_src)
# force the record's created far enough back that next_state -> expired (idle: never claimed/run)
rp = os.path.join(PQ_ROOT, "idle", "id1.held.json")
_rec = json.load(open(rp))
_rec["created"] = 0.0   # epoch — older than DEFER_TTL_S
Q._atomic_write(rp, _rec)
burned = PD.expire_idle()
check("expire_idle burns a TTL-exhausted idle private session (the Condition-4 gap)", burned == ["idle"])
check("expire_idle left no subdir trace", not os.path.exists(os.path.join(PQ_ROOT, "idle")))
check("expire_idle burned the dream tmpfs sink too", not os.path.exists(os.path.join(PRIV_ROOT, "idle")))
check("expire_idle: NO review.json anywhere (idle aging never reaches review)", _no_review_anywhere())
# a NON-stale idle hold is NOT burned by expire_idle (only TTL-crossed ones age out)
_make_private("fresh")
PQ.hold("fresh", "fr1", "Private create", png_src)
burned = PD.expire_idle()
check("expire_idle leaves a fresh (non-stale) hold alone", "fresh" not in burned
      and os.path.isfile(os.path.join(PQ_ROOT, "fresh", "fr1.held.json")))

# ============================ an injected runner exception never loses/raises ============================
_clean_board()
_make_private("boom_s")
PQ.hold("boom_s", "bm1", "Private create", png_src)


def boom(rec):
    raise RuntimeError("simulated private runner crash")


raised = False
try:
    ran = PD.drain_private_once(boom)
except Exception:
    raised = True
check("a runner exception NEVER raises out of drain_private_once", not raised)
check("a runner exception is caught -> a failure cause, the request re-held (never lost)",
      ran == [("bm1", "priv-drain-error", "held")]
      and os.path.isfile(os.path.join(PQ_ROOT, "boom_s", "bm1.held.json")))

# ============================ crash recovery: an orphaned running record returns to held ============================
_clean_board()
_make_private("crash_s")
PQ.hold("crash_s", "cr1", "Private create", png_src)
PQ.claim("crash_s", "cr1")   # leaves cr1.running.json with no live drainer — simulate a crashed tick
ran = PD.drain_private_once(runner_returning("done"))
check("crash recovery: no orphaned running record survives",
      not os.path.isfile(os.path.join(PQ_ROOT, "crash_s", "cr1.running.json")))
rec = json.load(open(os.path.join(PQ_ROOT, "crash_s", "cr1.held.json")))
check("crash recovery: the orphan is recovered to held with attempts bumped", rec["attempts"] == 1)
check("crash recovery: a recovered (preempted) job backs off before re-running this tick", ran == [])

# ============================ THE PRIVACY ASSERTION: nothing durable, no review, ever ============================
check("PRIVACY: NOTHING was ever written to the durable disk-backed spool", _no_durable_trace())
check("PRIVACY: NO review.json was EVER written by this module (durable + priv-queue clean)",
      _no_review_anywhere())
# source-level: the module never IMPORTS the review writer nor the durable writeback (Condition 2). We
# check for the actual import STATEMENT / module binding, not any prose mention — the module legitimately
# DOCUMENTS its refusal to touch lucid_review, so the word appears in docstrings; what must never appear
# is a real import line or a call through it. (Module names assembled non-contiguously so this test
# file's own assertion strings don't false-positive against themselves.)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lucid_priv_drain.py")).read()
_rev_mod = "lucid_" + "review"
check("PRIVACY: module has NO `import lucid_review` statement",
      ("import " + _rev_mod) not in src and ("import " + _rev_mod + " ") not in src)
check("PRIVACY: lucid_review is NOT importable into the module namespace (no binding)",
      not hasattr(PD, "R") and not hasattr(PD, _rev_mod))
# the module imports ONLY the ephemeral private queue + the governed launcher + the prompt gate — never
# lucid_queue's durable writeback / needs_review_items, and never lucid_review.
check("PRIVACY: module never binds lucid_queue (durable writeback/needs_review_items unreachable)",
      not hasattr(PD, "Q"))
check("PRIVACY: module DOES drive the ephemeral private queue (the SILENT-BURN writeback)",
      hasattr(PD, "PQ") and PD.PQ is PQ)
# it must not actually WRITE a review sidecar. Rather than a blunt substring scan (the module legitimately
# DOCUMENTS its refusal, so the filename appears in prose), the runtime assertions above already prove NO
# review.json is ever produced (_no_review_anywhere on every terminal) — the structural import checks here
# prove the WRITER is not even reachable. Together: no review writer imported, none produced at runtime.
_dur = "agentos" + "/" + "lucid-queue"
check("PRIVACY: module never names the durable lucid-queue path (Condition 2 grep)", _dur not in src)

# ============================ run_in_session: the daemon-thread loop drains + ages, stops cleanly ============================
_clean_board()
_make_private("thr_done")
PQ.hold("thr_done", "td1", "Private create", png_src)
_make_private("thr_idle")
PQ.hold("thr_idle", "ti1", "Private create", png_src)
_rp = os.path.join(PQ_ROOT, "thr_idle", "ti1.held.json")
_r = json.load(open(_rp))
_r["created"] = 0.0                       # epoch -> past DEFER_TTL_S, so expire_idle() burns it
_r["next_retry_after"] = time.time() + 1e9   # far-future backoff floor -> drain SKIPS it (so it can
#   only leave via expire_idle, proving the loop's expire path — not the drain path — ages idle holds)
Q._atomic_write(_rp, _r)
stop = threading.Event()
thr = threading.Thread(target=PD.run_in_session, args=(stop,),
                       kwargs={"interval_s": 0.05, "runner": runner_returning("done")},
                       daemon=True, name="lucid-priv-drain-test")
thr.start()
deadline = time.monotonic() + 3.0
while time.monotonic() < deadline:
    drained = not os.path.isfile(os.path.join(PQ_ROOT, "thr_done", "td1.held.json"))
    aged = not os.path.exists(os.path.join(PQ_ROOT, "thr_idle"))
    if drained and aged:
        break
    time.sleep(0.02)
stop.set()
thr.join(timeout=2.0)
check("run_in_session drains a held job (the loop calls drain_private_once)",
      not os.path.isfile(os.path.join(PQ_ROOT, "thr_done", "td1.held.json")))
check("run_in_session ages out an idle stale hold (the loop calls expire_idle)",
      not os.path.exists(os.path.join(PQ_ROOT, "thr_idle")))
check("run_in_session stops cleanly when the stop_event is set", not thr.is_alive())

# ============================ the REAL _governed_private_runner: private=True, best-effort, fail-open ============================
# Monkeypatch the module-level lucid_linear (PD.L) and lucid_safety (PD.S) with fakes so no GPU, lease,
# or daemon is touched; assert the contract AND that the governed path is driven as the PRIVATE create
# surface does (start private=True from the SEALED snapshot, _trusted_seed=True, gated step, best-effort).
_real_L, _real_S = PD.L, PD.S


class _FakeLinear:
    def __init__(self, step_returns=None, start_raises=False):
        self.step_returns = step_returns
        self.start_raises = start_raises
        self.start_calls = []
        self.step_calls = []
        self.wait_turn_calls = []

    def start(self, session, image, private=False, _trusted_seed=False, **kw):
        self.start_calls.append({"session": session, "image": image,
                                 "private": private, "_trusted_seed": _trusted_seed})
        if self.start_raises:
            raise RuntimeError("simulated lease/start blowup")
        return {"session": session, "nodes": [{"id": 0}]}

    def wait_turn(self, tier=None, est=None):
        # ADR-0041: the private drainer parks on the arbiter before step (best-effort, fail-open).
        self.wait_turn_calls.append({"tier": tier, "est": est})
        return True

    def step(self, session, prompt, label=None, tier=None):
        self.step_calls.append({"session": session, "prompt": prompt, "label": label, "tier": tier})
        return self.step_returns


class _FakeSafety:
    def __init__(self, gate_returns="gated-motion"):
        self._gate_returns = gate_returns

    def gate_prompt(self, text):
        return self._gate_returns


def _run_governed(rec, *, step_returns=None, start_raises=False, gate_returns="gated-motion"):
    fakeL = _FakeLinear(step_returns=step_returns, start_raises=start_raises)
    PD.L, PD.S = fakeL, _FakeSafety(gate_returns=gate_returns)
    try:
        outcome = PD._governed_private_runner(rec)
    finally:
        PD.L, PD.S = _real_L, _real_S
    return outcome, fakeL


_snap = os.path.join(_TMP, "gov_snap.png")
with open(_snap, "wb") as _f:
    _f.write(b"\x89PNG\r\n")
_grec = {"id": "gov_x", "snapshot": _snap}

out, fl = _run_governed(_grec, step_returns={"id": 1, "clip": "/x.mp4"})
check("governed private runner: a produced node -> done", out == "done")
check("governed private runner: re-runs FROM THE SEALED SNAPSHOT (never a URL)", fl.start_calls[0]["image"] == _snap)
check("governed private runner: session id is the record id", fl.start_calls[0]["session"] == "gov_x")
check("governed private runner: PRIVATE=TRUE (renders to sealed sinks, never shared disk)",
      fl.start_calls[0]["private"] is True)
check("governed private runner: B2 not re-run (_trusted_seed=True, no consent edge)",
      fl.start_calls[0]["_trusted_seed"] is True)
check("governed private runner: drains on a best-effort lease (fail-open by construction)",
      fl.step_calls[0]["tier"] == "best-effort")
check("governed private runner: steps with the GATED motion prompt", fl.step_calls[0]["prompt"] == "gated-motion")
# ADR-0041: the private drainer also parks behind the cross-workflow VRAM queue (best-effort) before acquiring.
check("governed private runner: parks on the arbiter (WaitTurn) before stepping",
      len(fl.wait_turn_calls) == 1 and fl.wait_turn_calls[0]["tier"] == "best-effort")

out, _ = _run_governed(_grec, step_returns=None)
check("governed private runner: step returns None -> gpu-busy (fail-open)", out == "gpu-busy")

out, fl = _run_governed(_grec, gate_returns=None)
check("governed private runner: a blocked motion prompt -> failure cause, step never attempted",
      out == "prompt-blocked" and not fl.step_calls)

try:
    out, _ = _run_governed(_grec, start_raises=True)
    check("governed private runner: an internal exception -> failure cause, not a raise", out == "priv-drain-error")
except Exception:
    check("governed private runner: an internal exception -> failure cause, not a raise", False)

out, fl = _run_governed({"id": "gov_y", "snapshot": "/no/such/file.png"})
check("governed private runner: a missing snapshot -> failure cause, start not called",
      out == "no-snapshot" and not fl.start_calls)

# --- cleanup ---
import shutil  # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)

print(f"lucid_priv_drain: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
