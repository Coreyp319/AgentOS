#!/usr/bin/env python3
"""Lucid EPHEMERAL PRIVATE request queue (ADR-0019 §5, H1-H5) — the in-session, tmpfs-only retry
hold for PRIVATE "Create Video from Image" requests that couldn't run now (GPU busy / coordinator
down / ComfyUI cold).

This is the SECOND, parallel, RAM-only store the privacy ruling required (ADR-0019 §5): a
physically-separate sibling of the durable `lucid_queue` spool, which the durable code path can
NEVER reach. It exists so a private request gets the human's chosen ephemeral-in-session auto-retry
WITHOUT ever touching a disk-backed/persisted store or any ambient/review surface — a private item
that exhausts its retries or ages out **burns SILENTLY** (it never becomes needs-review).

PHYSICAL SEPARATION (Condition 2, the grep invariant): this module names ONLY the ephemeral root
`$XDG_RUNTIME_DIR/agentos/lucid-priv-queue/` and NEVER the durable spool's path. The durable spool's
own path constant lives entirely in `lucid_queue.durable_dir()`; we never import or reference it, and
no source token here spells that durable path — so a reviewer's `grep` finds this file naming only
the priv root, never both roots.
A future "unify the queues" PR would have to delete one module and merge two refusal asserts — a
loud, reviewable, ADR-requiring act.

WHERE RECORDS LIVE (the burn-alignment that needs no edit to lucid_store.py):
  $XDG_RUNTIME_DIR/agentos/lucid-priv-queue/        = `lucid_store._priv_queue_root()` (the SAME root)
    └── <session>/                                  = ONE subdir per private session (mode 0700)
          ├── <id>.held.json / .running.json        = the queue records (lucid_queue's suffix idiom)
          ├── <id>.png                              = the snapshot, sealed into THIS tmpfs subdir
          └── .seq / .seq.lock                      = the per-session arrival counter (alloc_seq)
Because each private session is a SUBDIR under the priv-queue root, the already-landed Condition-1
burn finds it for free: `lucid_store.list_priv_queue()` enumerates exactly these session subdirs, so
the on-logout `for s in list_priv_queue() ∪ list_private(): burn(s)` loop burns each session's dream
sinks, and `clear_priv_queue_dir()` rmtrees the whole priv-queue root (records + per-session subdirs)
as the final sweep — with NO second edit to `lucid_store.py`.

REUSE: the spool-agnostic ops in `lucid_queue` (read_held / claim / writeback's record I/O via the
pure `next_state` / `retry_backoff_s`, recover_crashed, drain_order, alloc_seq) all take a `spool`
dir, so we pass each session's subdir as the spool and reuse them verbatim. We do NOT reimplement
them and do NOT edit `lucid_queue`. The ONE thing we do NOT reuse is `lucid_queue.writeback`'s
terminal branch: its `next_state` can return "needs-review" and file a `.review.json` (the durable
review lane). A private item is structurally ineligible for review (H3), so our own writeback
wrapper intercepts the "needs-review"/"expired" terminal and BURNS the session silently instead.

SECURITY: session names flow into filesystem paths (incl. an rmtree target via burn/purge), so every
name is validated through `lucid_store.valid_session` (the same fail-closed allowlist) before any
path is built. Job ids reuse `lucid_queue`'s `_ID_RE` guard inside its `_rec_path`.
"""
import glob
import json
import os
import shutil
import time

import lucid_queue as Q   # spool-agnostic ops (read_held/claim/recover_crashed/drain_order/alloc_seq/
#                            next_state/retry_backoff_s); we pass a per-session dir as the `spool`.
import lucid_store as ST   # the PRIVATE gate (is_private), the shared session-name allowlist, the
#                            on-logout burn — and, critically, the priv-queue ROOT we must align to.

# We deliberately import NO durable-path symbol. The grep invariant (ADR-0019 §5 Condition 2) is that
# this file references `lucid-priv-queue` and NEVER spells the durable spool's path token. The reused
# `lucid_queue` ops never hardcode the durable dir — they only ever touch the `spool` we pass.


# ============================ the ephemeral root (the ONLY path this module names) ============================
def priv_queue_root():
    """The tmpfs root for the ephemeral private queue — IDENTICAL to `lucid_store._priv_queue_root()`
    so the landed Condition-1 burn already covers it. We resolve it the same way (XDG_RUNTIME_DIR,
    falling back to /run/user/<uid>) rather than importing the private `_priv_queue_root` symbol, and
    assert agreement at call time so a drift in either module fails loudly, not silently."""
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    root = os.path.join(base, "agentos", "lucid-priv-queue")
    # Belt-and-suspenders: the burn alignment is load-bearing, so prove the two roots agree here.
    assert root == ST._priv_queue_root(), (
        f"priv-queue root drift: {root!r} != lucid_store._priv_queue_root() "
        f"{ST._priv_queue_root()!r} — the on-logout burn would miss the queue (ADR-0019 Condition 1)")
    return root


def _session_spool(session):
    """The per-session subdir that serves as the `spool` for the reused `lucid_queue` ops. One subdir
    per session is exactly what `lucid_store.list_priv_queue()` enumerates — that IS the burn hook."""
    if not ST.valid_session(session):
        raise ValueError(f"invalid private session {session!r} (allowed: [A-Za-z0-9_-], 1-64 chars)")
    return os.path.join(priv_queue_root(), session)


def _ensure_sealed_session(session):
    """Create the per-session subdir (and the priv-queue root) as 0700 dirs we own. Mirrors
    `lucid_store.ensure_session`'s seal: chmod the subdir AND its parent (the root) to 0700."""
    spool = _session_spool(session)
    os.makedirs(spool, mode=0o700, exist_ok=True)
    os.chmod(spool, 0o700)
    os.chmod(os.path.dirname(spool), 0o700)   # the lucid-priv-queue root, too
    return spool


# ============================ the EPHEMERAL chokepoint ============================
def hold(session, job_id, title, snapshot_src=None):
    """THE ephemeral chokepoint (ADR-0019 §5) — the ONLY writer to the tmpfs private queue.

    Asserts `ST.is_private(session)` and RAISES `ValueError` otherwise: a non-private session can
    never enter the private spool (Condition 2, physical separation — the durable `enqueue` is the
    mirror refusal). Snapshots the already-sanitized PNG INTO the sealed tmpfs session subdir (never
    shared disk, never a drain-time URL re-fetch). Writes a `held` record mirroring `lucid_queue`'s
    record shape, with `private=True` and a fresh per-session monotonic `seq`. Returns the record."""
    if job_id is None:
        raise ValueError("hold: job_id is None — even a private hold needs a stable dedup id")
    if not ST.is_private(session):
        raise ValueError(
            f"hold: session {session!r} is NOT private — a non-private request must never enter the "
            f"ephemeral private spool (ADR-0019 §5 Condition 2). Use lucid_queue.enqueue for durable.")
    spool = _ensure_sealed_session(session)
    now = time.time()
    snapshot = None
    if snapshot_src and os.path.isfile(snapshot_src):
        snapshot = Q._snapshot_path(spool, job_id)        # reuse the id-validated snapshot path
        shutil.copyfile(snapshot_src, snapshot)           # into the SEALED tmpfs subdir, never shared
        os.chmod(snapshot, 0o600)
    rec = {"id": job_id, "seq": Q.alloc_seq(spool), "created": now, "title": str(title)[:80],
           "snapshot": snapshot, "private": True, "attempts": 0, "last_error": None,
           "next_retry_after": 0.0, "state": "held"}
    Q._atomic_write(Q._rec_path(spool, job_id, "held"), rec)
    return rec


# ============================ the lifecycle (reused ops + a SILENT-BURN terminal) ============================
def read_held(session):
    """Every `held` record for a private session — the reused `lucid_queue.read_held` over the
    per-session spool (a `running` record is invisible: single-flight)."""
    return Q.read_held(_session_spool(session))


def drain_order(session):
    """Strict arrival FIFO by `seq` for one private session (reused `lucid_queue.drain_order`; HALTS on
    a priority key — the anti-scheduler invariant holds for the private queue too)."""
    return Q.drain_order(read_held(session))


def claim(session, job_id):
    """Atomic held→running claim (reused `lucid_queue.claim`) over the per-session spool."""
    return Q.claim(_session_spool(session), job_id)


def recover_crashed(session):
    """Return any orphaned `*.running.json` for a private session to `held` (reused
    `lucid_queue.recover_crashed`). Decides from the FILE, never the PID. NOTE: like writeback, the
    reused op can re-file a record as `*.review.json` if its `next_state` is "needs-review" — a
    private item must not acquire a review row, so we sweep any such file to a silent burn afterward.
    Returns (recovered_list, burned_bool)."""
    spool = _session_spool(session)
    recovered = Q.recover_crashed(spool)
    # H3/Condition 6: a private record may NEVER rest as needs-review (a review row). If recovery
    # re-filed one, the whole session's retry has exhausted into the human lane it cannot have —
    # burn it silently rather than leave a review artifact in the spool.
    if _has_review_record(spool):
        purge(session)
        return recovered, True
    return recovered, False


def writeback(session, rec, outcome):
    """Resolve a `running` private record by its run outcome — the EPHEMERAL counterpart to
    `lucid_queue.writeback`, diverging at exactly one point: the durable writeback escalates an
    exhausted/human-cause record to `needs-review` (a persisted review row + the `review.json` warm
    lane). A private item is structurally ineligible for that (H3), so when the PURE policy
    (`lucid_queue.next_state`, reused unchanged) computes a terminal that is NOT a live retry, we
    BURN THE SESSION SILENTLY (Condition 6): no review row, no review.json, no persisted trace.

    Returns the resolved disposition: "done" | "held" | "burned-silent"."""
    spool = _session_spool(session)
    running = Q._rec_path(spool, rec["id"], "running")
    if outcome == "done":
        Q._unlink(running)
        Q._unlink(Q._snapshot_path(spool, rec["id"]))
        return "done"
    rec["attempts"] = rec.get("attempts", 0) + 1
    rec["last_error"] = outcome
    age = time.time() - rec.get("created", time.time())
    state = Q.next_state(rec["attempts"], rec["last_error"], age)   # PURE policy, reused verbatim
    Q._unlink(running)
    if state == "held":
        # still a live in-session retry — re-file held with the backoff floor (reused policy).
        rec["next_retry_after"] = time.time() + Q.retry_backoff_s(rec["attempts"])
        rec["state"] = "held"
        Q._atomic_write(Q._rec_path(spool, rec["id"], "held"), rec)
        return "held"
    # state is "needs-review" or "expired" → a private item has NO such terminal. Burn the whole
    # session silently. We do NOT write a `.review.json`, do NOT call lucid_review, do NOT increment
    # any count — the forgotten private retry dies quietly inside the session.
    purge(session)
    return "burned-silent"


def expire_stale(session, *, now=None):
    """Sweep a private session's held records for TTL exhaustion WITHOUT running them — the no-run
    aging path (a session left holding past `DEFER_TTL_S`). Any held record whose age crosses the
    pure-policy terminal (`next_state` → "expired"/"needs-review") triggers a SILENT BURN of the whole
    session (Condition 6). Returns True iff the session was burned. (A live drainer would normally
    reach exhaustion via writeback; this covers the forgotten/idle session that never gets claimed.)"""
    spool = _session_spool(session)
    if not os.path.isdir(spool):
        return False
    now = time.time() if now is None else now
    for rec in Q.read_held(spool):
        age = now - rec.get("created", now)
        state = Q.next_state(rec.get("attempts", 0), rec.get("last_error"), age)
        if state in ("expired", "needs-review"):
            purge(session)
            return True
    return False


# ============================ the SILENT burn / no-trace purge ============================
def _has_review_record(spool):
    """True iff a `*.review.json` exists in the spool — a record that wrongly entered the durable
    review lane. A private spool must never contain one; its presence forces a silent burn."""
    return bool(glob.glob(os.path.join(spool, "*.review.json")))


def purge(session):
    """Remove a private session's ENTIRE ephemeral hold, leaving NO trace (Condition 6 / H1). This is
    the silent terminal — it does the dream-sink burn (`lucid_store.burn`, symlink-aware, verifies
    removal: the tmpfs dream dir + any sealed input subdir + any output leftovers from an in-flight
    retry) AND rmtrees this session's queue subdir (the held/running records + the snapshot + the seq
    counter). It NEVER writes a review row, NEVER calls `lucid_review`, NEVER touches a count. Returns
    the queue-subdir path if one was removed, else None.

    SECURITY: `_session_spool` validates the name before any path is built; the queue subdir is
    rmtree'd only if it is a real dir we own (never followed through a planted symlink)."""
    spool = _session_spool(session)              # validates the session name (fail-closed)
    ST.burn(session)                             # the dream sinks (tmpfs/input/output) — read-only reuse
    if ST._own_real_dir(spool) is True:          # the queue subdir: only if a real dir we own
        shutil.rmtree(spool, ignore_errors=True)
        return spool if not os.path.exists(spool) else None
    if os.path.islink(spool):                    # a planted symlink at the subdir: drop the link only
        try:
            os.unlink(spool)
        except OSError:
            pass
    return None


# ============================ surfacing helpers (RAM-derived, request-time only) ============================
def list_sessions():
    """Live private sessions holding an ephemeral request — the per-session subdirs under the
    priv-queue root. By construction this AGREES with `lucid_store.list_priv_queue()` for the same
    root (both enumerate `valid_session` subdirs of `_priv_queue_root()`); the test pins the
    agreement so the on-logout burn provably covers every session this module creates."""
    root = priv_queue_root()
    if not os.path.isdir(root):
        return []
    return sorted(s for s in os.listdir(root)
                  if ST.valid_session(s) and os.path.isdir(os.path.join(root, s)))


def held_count():
    """An in-session, RAM-derived count of private held requests across all sessions, computed ON READ
    from a directory listing (the `list_private()` idiom). For the loopback-only `:8765` panel ONLY.
    FORBIDDEN (ADR-0019 §5): writing this to `agent.json` / `keyhole.json` / `pending_requests` /
    `needs_you.json`. This function only RETURNS the integer; it never serializes it anywhere."""
    n = 0
    for s in list_sessions():
        spool = os.path.join(priv_queue_root(), s)
        n += len(glob.glob(os.path.join(spool, "*.held.json")))
    return n
