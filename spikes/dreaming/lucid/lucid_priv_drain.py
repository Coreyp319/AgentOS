#!/usr/bin/env python3
"""Lucid PRIVATE in-session drainer (ADR-0019 §5, Condition 4) — the in-process, in-session companion
to the durable `lucid_drain.py`, for the EPHEMERAL private spool (`lucid_priv_queue`).

WHY THIS EXISTS (the Condition-4 gap a prior agent flagged): a private hold must NEVER outlive the
session and must never rely SOLELY on the on-logout burn to die. The durable drainer is a systemd
--user oneshot TIMER (it can run after logout, writes a `review.json` sidecar, escalates to
needs-review). NONE of that is allowed for a private item (H1-H3): no persisted/ambient/review
surface, silent terminals only, and the spool is tmpfs that the on-logout burn wipes. So the private
side needs a drainer that:
  * runs WHILE THE USER IS LOGGED IN, as a daemon thread INSIDE the lucid web process — so it lives and
    dies with the session and is itself burned on logout (no systemd unit, no after-logout window);
  * re-runs eligible private holds AND ages out idle ones BEFORE logout (so an idle hold that is never
    claimed still burns silently in-session, not just at the logout sweep);
  * on EVERY private terminal (retry-exhausted, human-cause, or TTL) SILENT-BURNS the session — NEVER
    a `review.json`, never a needs-review row, never a warm bloom, never `lucid_review`.

WHAT IT REUSES vs WHAT IT REFUSES:
  * REUSES `lucid_priv_queue` end-to-end — its `recover_crashed`/`drain_order`/`read_held`/`claim` are
    the spool-agnostic `lucid_queue` ops over the per-session subdir, and CRITICALLY its `writeback`
    and `expire_stale` are the SILENT-BURN terminals (they call `purge`, never `lucid_review`). We do
    the loop; the private queue owns the terminal so the privacy invariant lives in one module.
  * REFUSES, by construction, every durable-side coupling: this module NEVER imports `lucid_review`,
    NEVER imports `lucid_queue.writeback`/`needs_review_items`, NEVER writes `review.json`, never names
    the durable spool path. The grep invariant (Condition 2) holds: we touch only `lucid_priv_queue`.

DRAIN SCOPE — ONE PER SESSION PER TICK (not one global): each tick, every live private session FIFO-
drains at most ONE eligible held, so a busy session can't starve another's aging, and the per-session
flock keeps each session single-flight. The durable drainer is one-global-per-fire because the systemd
timer re-fires for the rest; here a single in-process tick visits every session, so per-session is the
natural, fair shape and still bounds work per tick (a session re-runs at most once per interval).

SINGLE-FLIGHT: a per-session flock (`.priv-drain.lock` inside each session subdir) guards a session's
drain, mirroring the durable drainer's spool flock — so two ticks (or a future second consumer) never
drain the same session at once, and `recover_crashed` runs provably under the lock. A session whose
lock is already held this tick is skipped (its other holder is mid-drain), never blocked on.

FAIL-OPEN (ADR-0003): an injected runner exception is caught and re-held (never lost, never raised); a
burned-out session simply disappears; an unreadable session is skipped, never crashes the thread. The
loop runs `best-effort` so Tier::Interactive structurally preempts a private re-run too.

WIRING INTO lucid_web.py (paste these 3-4 lines into `main()` when that file is quiet — do NOT edit it
from here; it is being edited in another session). After the web app is built and BEFORE the blocking
serve call, start the daemon thread; on shutdown, set the stop event:

    import threading, lucid_priv_drain as PD          # at the top with the other imports
    _priv_stop = threading.Event()
    _priv_thr = threading.Thread(target=PD.run_in_session, args=(_priv_stop,),
                                 daemon=True, name="lucid-priv-drain")
    _priv_thr.start()
    try:
        app.run(...)          # the existing blocking serve call, unchanged
    finally:
        _priv_stop.set()      # ask the drainer to stop; daemon=True burns it on a hard exit anyway

The thread is `daemon=True` so a hard process exit (the typical logout kill of the web process) tears
it down WITH the session — the on-logout burn then wipes the whole tmpfs queue regardless, so the
drainer needs no clean-shutdown guarantee to stay airtight (it only makes idle holds die SOONER).
"""
import fcntl
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_priv_queue as PQ   # noqa: E402  the EPHEMERAL spool: list_sessions/recover_crashed/
#   drain_order/read_held/claim/writeback (SILENT-BURN terminal)/expire_stale. The ONLY queue module
#   we touch — we deliberately import NO durable-side symbol (no lucid_queue.writeback, no lucid_review).
import lucid_linear as L        # noqa: E402  the governed launcher (lease + red-line gate + B2 chokepoint)
import lucid_safety as S        # noqa: E402  the deterministic prompt gate — same chokepoint as the web loop

# The neutral "come alive" motion prompt the interactive create-from-image path uses (mirrors
# lucid_drain.MOTION_PROMPT / create_from_image.MOTION_PROMPT). It still passes through S.gate_prompt
# below like every other beat — the private drainer earns no exemption from the red-line gate (B3).
MOTION_PROMPT = ("the scene gently comes alive with subtle natural motion and a slow, calm "
                 "cinematic camera drift; gentle ambient light")

DEFAULT_INTERVAL_S = 30   # how often the in-session loop drains + ages; tunable, not calibrated.


def _session_lock(session):
    """An exclusive flock over a per-session `.priv-drain.lock` — single-flight for one session's
    drain (mirrors the durable drainer's spool flock). Returns the open file handle on success (caller
    must keep it open for the critical section, then close to release), or None if another holder is
    mid-drain this tick (skip the session, never block). The lock file lives inside the session subdir,
    so it is wiped by the same `purge`/on-logout burn as the records — it leaves no trace."""
    try:
        spool = PQ._session_spool(session)        # validates the session name (fail-closed)
    except ValueError:
        return None
    if not os.path.isdir(spool):
        return None                               # the session burned out from under us — nothing to do
    lock = open(os.path.join(spool, ".priv-drain.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None                               # another tick/consumer is mid-drain — skip, don't block
    return lock


def drain_private_one(session, runner, *, now=None):
    """Drain ONE eligible held for a SINGLE private session under its per-session flock: recover orphans
    → FIFO-by-seq → claim + run ONE eligible held via the injected `runner(rec)->outcome` → SILENT-BURN
    writeback. Returns (id, outcome, disposition) for the job run, else None (nothing eligible / session
    burned / lock contended). `now` (ADR-0036 I9): inject the clock for a replayable tick; None ⇒ wall.

    Every terminal here is `lucid_priv_queue.writeback`, whose non-retry branch calls `purge` (the
    silent burn): a retry-exhausted or human-cause private record can ONLY end as "burned-silent" or a
    live "held" — there is no review/needs-review/ambient edge reachable from this function."""
    now = time.time() if now is None else now
    lock = _session_lock(session)
    if lock is None:
        return None
    try:
        # crash recovery first, provably under our flock. recover_crashed itself silently burns the
        # session if recovery would have re-filed a record as needs-review (it never leaves a review row).
        _recovered, burned = PQ.recover_crashed(session, now=now)
        if burned:
            return (session, "recover-burned", "burned-silent")
        for rec in PQ.drain_order(session):           # strict arrival FIFO (anti-scheduler holds here too)
            if rec.get("next_retry_after", 0) > now:
                continue                              # backoff floor — eligibility, NOT an ordering key
            claimed = PQ.claim(session, rec["id"])
            if claimed is None:
                continue                              # lost the race to a concurrent claim
            try:
                outcome = runner(claimed)
            except Exception:
                outcome = "priv-drain-error"          # never lose the request to a runner exception
            disp = PQ.writeback(session, claimed, outcome, now=now)   # SILENT-BURN terminal (never review.json)
            return (rec["id"], outcome, disp)
        return None                                   # ONE eligible job per session per tick
    finally:
        lock.close()                                  # releases the per-session flock


def drain_private_once(runner):
    """ONE tick across ALL live private sessions (`lucid_priv_queue.list_sessions()`): each session
    FIFO-drains at most ONE eligible held this tick (see DRAIN SCOPE in the module docstring). Returns
    the list of (id, outcome, disposition) tuples for the jobs run this tick (possibly empty).

    `runner(rec) -> outcome` is INJECTED so the loop is testable without a GPU/lease/daemon. Defensive:
    a single session blowing up is isolated (caught + logged) so it never stops the rest of the sweep —
    the in-session thread must keep ticking."""
    ran = []
    for session in PQ.list_sessions():
        try:
            r = drain_private_one(session, runner)
            if r is not None:
                ran.append(r)
        except Exception as e:                        # isolate a bad session; keep draining the others
            print(f"lucid-priv-drain: drain error on session {session!r}: {e}",
                  file=sys.stderr, flush=True)
    return ran


def expire_idle(*, now=None):
    """THE Condition-4 gap: age out idle private holds BEFORE logout. For every live private session,
    call `lucid_priv_queue.expire_stale(session)` — which SILENT-BURNS any session holding a record
    past its TTL WITHOUT running it (the forgotten/idle hold that is never claimed). Returns the list of
    sessions burned this tick. Defensive per-session so one failure never stops the aging sweep.

    This is what makes a private hold not depend SOLELY on the logout burn: an idle session crosses
    DEFER_TTL_S and dies silently in-session, the same way a logged-out session would have."""
    burned = []
    for session in PQ.list_sessions():
        try:
            if PQ.expire_stale(session, now=now):
                burned.append(session)
        except Exception as e:
            print(f"lucid-priv-drain: expire error on session {session!r}: {e}",
                  file=sys.stderr, flush=True)
    return burned


def run_in_session(stop_event, *, interval_s=DEFAULT_INTERVAL_S, runner=None):
    """The daemon-thread loop: every `interval_s`, run `drain_private_once` (re-run one eligible held
    per live session) THEN `expire_idle` (age out idle holds), until `stop_event` is set. Designed to be
    started as a `daemon=True` thread INSIDE the lucid web process (see the wiring snippet in the module
    docstring) so it lives and dies with the session — burned on logout with everything else.

    `runner` defaults to the real `_governed_private_runner`; tests inject a fake. `stop_event.wait`
    (not time.sleep) is the cadence so a shutdown wakes the loop promptly. Fail-open: an exception in a
    tick is caught and logged — the loop keeps ticking; the thread must not die and strand idle holds.

    ORDER MATTERS: drain first, then expire. Draining can resolve a session ("done" clears it); expiring
    then ages whatever idle holds remain. Either order is privacy-safe (both terminals are silent), but
    drain-then-expire avoids burning a session in the same tick a still-eligible retry would have run."""
    runner = runner or _governed_private_runner
    while not stop_event.is_set():
        try:
            drain_private_once(runner)
            expire_idle()
        except Exception as e:                        # the whole tick is wrapped: the thread never dies
            print(f"lucid-priv-drain: tick error: {e}", file=sys.stderr, flush=True)
        stop_event.wait(interval_s)                   # interruptible cadence (wakes on stop)


def _governed_private_runner(rec):
    """Re-run a held PRIVATE request through the governed launcher on a Tier::BestEffort lease, returning
    an outcome string ('done' on a produced node, else a failure cause). The PRIVATE counterpart to
    `lucid_drain._governed_runner`, differing in exactly one load-bearing flag: `private=True`.

    Mirrors the governed path the interactive private create-from-image surface drives: `L.start(...,
    private=True)` then a gated `L.step(..., tier="best-effort")`. Differences from the durable runner,
    all load-bearing:
      * `private=True` — the re-run renders to the SEALED private sinks (tmpfs dream dir + a sealed
        ComfyUI-input subdir) and the clip is moved to tmpfs, so NO private byte ever lingers on shared
        disk (lucid_store routes it; ADR-0016). The durable runner is `private=False`.
      * the seed is the EPHEMERAL spool SNAPSHOT (`rec["snapshot"]`, the sanitized PNG the private queue
        sealed into the tmpfs session subdir at hold) — NEVER a URL re-fetch.
      * `_trusted_seed=True` because B2 ALREADY cleared this seed before the hold (so we do NOT re-run
        B2 — it was the gate that admitted the request; and a private item must never re-enter a B2
        verdict that could surface a consent prompt).
      * `tier="best-effort"` so `arbitrate()` structurally Queues this re-run behind any holder and lets
        Tier::Interactive preempt it — fail-open BY CONSTRUCTION.

    Fail-open + defensive: a missing snapshot, a blocked motion prompt, `step` returning None (GPU busy /
    preempted / ComfyUI cold), or ANY exception all map to a failure-cause STRING; this runner never
    raises (drain_private_one catches too, but holding the seam closed here is cheap). A failure cause
    flows back through the SILENT-BURN writeback — there is no review edge from here."""
    try:
        snapshot = rec.get("snapshot")
        if not snapshot or not os.path.isfile(snapshot):
            return "no-snapshot"                      # the sealed snapshot vanished — re-hold, never re-fetch
        session = rec["id"]                            # the record id IS the session id for the re-run
        frozen = rec.get("frozen") or {}               # ADR-0036 D5: frozen prompt+seed from the sealed record
        # Unlike the durable ONESHOT drainer, we do NOT pin the engine (no E.set_engine): this runs
        # IN-PROCESS in the web app, so mutating the global engine could disrupt a concurrent live dream.
        # Private retries are in-session / short-lived, so registry drift within a session is unlikely.
        # B2 already cleared this seed at hold → _trusted_seed=True (do NOT re-run B2, no consent edge).
        L.start(session, snapshot, private=True, _trusted_seed=True, seed=frozen.get("seed"))
        gated = S.gate_prompt(frozen.get("prompt") or MOTION_PROMPT)   # frozen prompt; constant only for legacy
        if gated is None:                              # defensive: the neutral motion prompt should pass
            return "prompt-blocked"
        node = L.step(session, gated, label="animate", tier="best-effort")
        if node is None:                               # generate_video fell open (GPU busy / preempted)
            return "gpu-busy"
        return "done"
    except Exception as e:                             # fail-open: surface a cause, never raise out of here
        print(f"lucid-priv-drain: governed runner error on {rec.get('id')!r}: {e}",
              file=sys.stderr, flush=True)
        return "priv-drain-error"
