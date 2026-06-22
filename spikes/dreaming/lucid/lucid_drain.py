#!/usr/bin/env python3
"""Lucid drainer (ADR-0019 §5, G3) — a systemd --user oneshot TIMER (the timer is the clock, not a
daemon loop) that re-runs ONE held request per fire and re-files the outcome, so a request that
couldn't run when it arrived is run later instead of dropped.

Invariants (all from lucid_queue, the authoritative spool):
  * single-flight via flock — two timer fires never drain at once;
  * crash recovery from the FILE — any orphaned `*.running.json` at fire start is returned to held;
  * strict arrival FIFO by `seq` (G6) — never a priority order;
  * `Tier::BestEffort` for the run (so Tier::Interactive structurally preempts it — fail-open);
  * recompute the G1 `review.json` sidecar each fire from the authoritative needs-review set.

This is a POLLING drainer (~OnUnitInactiveSec worst-case latency). The lease-free EVENT (closed loop)
is G10/Phase-2, substrate-blocked on the daemon push (ADR-0012 §6).
"""
import fcntl
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_queue as Q       # noqa: E402
import lucid_review as R      # noqa: E402  (G1 sidecar producer — the SOLE review.json writer)
import lucid_linear as L      # noqa: E402  (the governed launcher — lease + red-line gate + B2 chokepoint)
import lucid_engine as E      # noqa: E402  (ADR-0036 D5: pin the frozen engine on the durable ONESHOT drain)
import lucid_safety as S      # noqa: E402  (the deterministic prompt gate — same chokepoint as the web loop)

# The neutral "come alive" motion prompt the interactive create-from-image path uses (mirrors
# create_from_image.MOTION_PROMPT). It still passes through S.gate_prompt below like every other
# beat — the drainer earns no exemption from the red-line gate (B3).
MOTION_PROMPT = ("the scene gently comes alive with subtle natural motion and a slow, calm "
                 "cinematic camera drift; gentle ambient light")


def _refresh_sidecar(spool):
    """Recompute review.json from the authoritative needs-review set every fire — never a cached
    scalar, so the warm bloom can only reflect what is really on the spool (G1 + G4 honesty)."""
    R.write(Q.needs_review_items(spool))


def drain_once(spool, runner, *, now=None):
    """One fire: recover orphans → FIFO drain → claim + run ONE eligible held → re-file the outcome →
    refresh the sidecar. `runner(rec) -> outcome` ('done' | a failure cause) is INJECTED so the loop is
    testable without a GPU. Returns (id, outcome, next_state) for the job run this fire, else None.
    `now` (ADR-0036 I9): inject the clock for a replayable tick (eligibility + recover + writeback all
    read it); None ⇒ wall clock."""
    now = time.time() if now is None else now
    Q.recover_crashed(spool, now=now)     # decides from the file; safe under the caller's flock
    ran = None
    for rec in Q.drain_order(Q.read_held(spool)):   # strict arrival FIFO (G6); HALTS on a priority key
        if rec.get("next_retry_after", 0) > now:
            continue                       # backoff floor — eligibility, NOT an ordering key
        claimed = Q.claim(spool, rec["id"])
        if claimed is None:
            continue                       # lost the race to a concurrent fire
        try:
            outcome = runner(claimed)
        except Exception:
            outcome = "drain-error"        # never lose the request to a runner exception
        next_state = Q.writeback(spool, claimed, outcome, now=now)
        ran = (rec["id"], outcome, next_state)
        break                              # ONE job per fire; the timer re-fires for the rest
    _refresh_sidecar(spool)
    return ran


def _governed_runner(rec):
    """Re-run a held request through the governed launcher on a Tier::BestEffort lease, returning an
    outcome string ('done' on a produced node, else a failure cause).

    Re-enters the SAME governed path the interactive create-from-image surface drives
    (create_from_image.py:301-308): `L.start(...)` then `L.step(...)`, with the prompt gated through
    `S.gate_prompt` — the drainer earns no exemption from the red-line gate. Differences from the
    interactive path, all load-bearing:
      * the seed is the spool SNAPSHOT (`rec["snapshot"]`, the sanitized PNG the queue copied at
        enqueue) — NEVER a URL re-fetch (ADR-0019: the durable spool owns its own image);
      * `_trusted_seed=True` because B2 ALREADY cleared this seed before enqueue (so we do NOT
        re-run B2 — it was the gate that admitted the request in the first place);
      * `tier="best-effort"` so `arbitrate()` structurally Queues this re-run behind any holder and
        lets Tier::Interactive preempt it — fail-open BY CONSTRUCTION (design doc G3).
    Fail-open + defensive: a missing snapshot, a blocked motion prompt, `step` returning None
    (GPU busy / preempted / ComfyUI cold), or ANY exception all map to a failure-cause STRING; this
    runner never raises (the drainer catches too, but holding the seam closed here is cheap)."""
    try:
        snapshot = rec.get("snapshot")
        if not snapshot or not os.path.isfile(snapshot):
            return "no-snapshot"                # the spool image vanished — re-hold, never re-fetch
        session = rec["id"]                      # the record id IS the session id for the re-run
        frozen = rec.get("frozen") or {}         # ADR-0036 D5: drain the FROZEN intent, not a live pointer
        # Pin the engine the request was ADMITTED under: a registry flip (e.g. wan→10eros) between enqueue
        # and this drain would otherwise run a different pipeline (and a wrong est) than the user asked for.
        # Safe here because the durable drainer is a systemd ONESHOT — no concurrent live dream in-process.
        eng = frozen.get("engine")
        if eng and eng != E.current_engine():
            E.set_engine(eng)
        # B2 already cleared this seed at enqueue → _trusted_seed=True (do NOT re-run B2). The frozen base
        # seed (when present) makes the deferred re-run reproduce the same noise family; None ⇒ legacy mint.
        L.start(session, snapshot, private=False, _trusted_seed=True, seed=frozen.get("seed"))
        gated = S.gate_prompt(frozen.get("prompt") or MOTION_PROMPT)   # frozen prompt; constant only for a legacy record
        if gated is None:                        # defensive: the neutral motion prompt should pass
            return "prompt-blocked"
        node = L.step(session, gated, label="animate", tier="best-effort")
        if node is None:                         # generate_video fell open (GPU busy / preempted)
            return "gpu-busy"
        return "done"
    except Exception as e:                       # fail-open: surface a cause, never raise out of here
        print(f"lucid-drain: governed runner error on {rec.get('id')!r}: {e}", file=sys.stderr, flush=True)
        return "drain-error"


def main(runner=None):
    spool = Q.durable_dir()
    os.makedirs(spool, mode=0o700, exist_ok=True)
    lock = open(os.path.join(spool, ".drain.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0                           # another fire is mid-drain — never two at once
    try:
        ran = drain_once(spool, runner or _governed_runner)
        if ran:
            print(f"lucid-drain: ran {ran[0]} → {ran[1]} (now {ran[2]})", flush=True)
        return 0
    finally:
        lock.close()                        # releases the flock


if __name__ == "__main__":
    sys.exit(main())
