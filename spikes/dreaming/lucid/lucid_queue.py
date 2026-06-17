#!/usr/bin/env python3
"""Lucid durable request queue (ADR-0019 §5, G3/G4/G6) — the spool that HOLDS a "Create Video from
Image" request that couldn't run now (GPU busy / coordinator down / ComfyUI cold) instead of dropping
it to a `skipped` tombstone. A held request is re-run later by `lucid_drain.py` on a Tier::BestEffort
lease, or escalated to `needs-review` for a human — it is NEVER silently lost.

This module owns the DURABLE spool only (`$XDG_DATA_HOME/agentos/lucid-queue/`, survives logout). The
ephemeral PRIVATE spool is a separate module (ADR-0019 §5, gated on the private-mode conditions) so no
source file names both paths — a private request physically cannot reach this persisted spool
(`enqueue` refuses `private=True`; Condition 2).

SOURCE-OF-TRUTH INVERSION (the load-bearing idea): the spool FILE is authoritative, the detached PID
is not. State lives in the filename SUFFIX — `<id>.held.json` / `<id>.running.json` / `<id>.review.json`
— so the atomic `os.rename` in `claim()` IS the held→running transition (single-flight without a DB),
and crash recovery decides from the file, never from `kill -0 owner_pid` (a recycled PID could lie).

ANTI-SCHEDULER (G6): the record carries `seq` (a persisted monotonic arrival ordinal — the ONLY
drain-order key) and `created` (wall-clock, DISPLAY ONLY). There is NO priority/weight/rank field; one
appearing HALTS the drainer (`SystemExit`). This buffer is a FIFO that defers — not a scheduler that
ranks (that would reinvent the orchestrator, ADR-0001).
"""
import fcntl
import glob
import json
import os
import re
import shutil
import tempfile
import time

# ---- G4 retry-policy constants (tunable knobs, NOT calibrated — ADR-0019 open risk: a Wan-14B dream
#      holds the lease ~1h, so DEFER_TTL_S/backoff may need widening once real cadence is measured) ----
MAX_ATTEMPTS = 5
DEFER_TTL_S = 6 * 3600        # a deferred (couldn't-run-now) request ages out after 6h
REVIEW_TTL_S = 24 * 3600      # a needs-review (human) request waits up to 24h
BACKOFF_BASE_S = 30
BACKOFF_CAP_S = 1800
HUMAN_ERRORS = ("consent-borderline", "b2-cant-verify")   # causes that need a human, not a retry

# ---- G6 anti-scheduler: any of these on a record means someone tried to build a scheduler. Halt. ----
_FORBIDDEN_ORDER_KEYS = ("priority", "weight", "rank", "urgency", "boost", "class")

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,71}$")
_STATES = ("held", "running", "review")   # filename suffixes; `expired`/`done` = unlink (terminal)


def durable_dir():
    """`$XDG_DATA_HOME/agentos/lucid-queue` (or `~/.local/share/...`). MUST match feed.rs::lucid_data_path
    so the drainer's `review.json` sidecar (G1) lands where `agentosd feed` reads it."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "agentos", "lucid-queue")


# ============================ G4 — pure retry policy (no clock/fs/model) ============================
def next_state(attempts, last_error, age_s):
    """PURE. The next state of a failed/deferred attempt. Precedence:
    expired > needs-review(human cause) > needs-review(retries exhausted) > held.
    A resource-exhausted request escalates to a human — it is NEVER dropped."""
    is_human = last_error in HUMAN_ERRORS
    ttl = REVIEW_TTL_S if is_human else DEFER_TTL_S
    if age_s >= ttl:
        return "expired"
    if is_human:
        return "needs-review"
    if attempts >= MAX_ATTEMPTS:
        return "needs-review"
    return "held"


def retry_backoff_s(attempts):
    """PURE. The post-failure eligibility floor (`next_retry_after`). NEVER an ordering key (G6): a
    backoff timer must not become a covert priority. Capped exponential."""
    return min(BACKOFF_BASE_S * (2 ** attempts), BACKOFF_CAP_S)


# ============================ G6 — anti-scheduler drain order ============================
def _assert_no_priority(jobs):
    for j in jobs:
        for k in _FORBIDDEN_ORDER_KEYS:
            if k in j:
                raise SystemExit(
                    f"ANTI-SCHEDULER INVARIANT VIOLATED: record {j.get('id')!r} carries forbidden "
                    f"ordering key {k!r}. The lucid queue is a FIFO deferral buffer, not a scheduler "
                    f"(ADR-0019 §5 / ADR-0001 — don't reinvent the orchestrator).")


def drain_order(jobs):
    """Strict arrival FIFO by `seq` — the ONLY drain-order key. `created` is display-only (clock skew
    makes it non-monotonic, so it must never sort). HALTS if any held record carries a priority-like key."""
    eligible = [j for j in jobs if j.get("state") == "held"]
    _assert_no_priority(eligible)
    return sorted(eligible, key=lambda j: j["seq"])


# ============================ spool I/O (spool-dir-parametrized; never hardcodes a path) ===========
def _ensure(spool):
    os.makedirs(spool, mode=0o700, exist_ok=True)
    return spool


def _rec_path(spool, job_id, state):
    if not _ID_RE.match(job_id or ""):
        raise ValueError(f"bad job id {job_id!r}")
    if state not in _STATES:
        raise ValueError(f"bad state {state!r}")
    return os.path.join(spool, f"{job_id}.{state}.json")


def _snapshot_path(spool, job_id):
    if not _ID_RE.match(job_id or ""):
        raise ValueError(f"bad job id {job_id!r}")
    return os.path.join(spool, f"{job_id}.png")


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _atomic_write(path, doc):
    fd, tmp = tempfile.mkstemp(prefix=".rec.", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        _unlink(tmp)
        raise


def alloc_seq(spool):
    """Monotonic arrival ordinal from a persisted `.seq` counter, allocated under an exclusive flock so
    two concurrent enqueues never collide. The ONLY drain-order key (G6)."""
    _ensure(spool)
    counter = os.path.join(spool, ".seq")
    with open(os.path.join(spool, ".seq.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(counter) as f:
                n = int(f.read().strip() or "0")
        except (OSError, ValueError):
            n = 0
        n += 1
        tmp = counter + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(n))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, counter)
        return n   # flock released on close


# ============================ the chokepoint + lifecycle ============================
def enqueue(job_id, title, snapshot_src=None, *, private=False, spool=None):
    """THE durable chokepoint (ADR-0019). Refuses `job_id is None` AND refuses `private=True` — a
    private/anonymous request can NEVER enter the persisted spool (Condition 2, physical separation).
    Snapshots the already-sanitized PNG INTO the spool so drain-time never re-fetches a remote URL.
    Writes a `held` record with a fresh monotonic `seq`. Returns the record."""
    if job_id is None:
        raise ValueError("enqueue: job_id is None — an anonymous request must not reach the durable spool")
    if private:
        raise ValueError("enqueue: private=True must use the ephemeral private queue, never the durable "
                         "spool (ADR-0019 Condition 2)")
    if not _ID_RE.match(job_id):
        raise ValueError(f"bad job id {job_id!r}")
    spool = durable_dir() if spool is None else spool
    _ensure(spool)
    now = time.time()
    snapshot = None
    if snapshot_src and os.path.isfile(snapshot_src):
        snapshot = _snapshot_path(spool, job_id)
        shutil.copyfile(snapshot_src, snapshot)   # the spool owns its own copy; URL re-fetch is forbidden
    rec = {"id": job_id, "seq": alloc_seq(spool), "created": now, "title": str(title)[:80],
           "snapshot": snapshot, "private": False, "attempts": 0, "last_error": None,
           "next_retry_after": 0.0, "state": "held"}
    _atomic_write(_rec_path(spool, job_id, "held"), rec)
    return rec


def read_held(spool):
    """Every `held` record (a `running` record is invisible to this glob — that's the single-flight)."""
    out = []
    for p in glob.glob(os.path.join(spool, "*.held.json")):
        try:
            with open(p) as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            continue
    return out


def claim(spool, job_id):
    """Atomic held→running: `os.rename(<id>.held.json, <id>.running.json)`. The rename IS the claim —
    exactly one drainer wins; a loser gets OSError and returns None. The winner stamps `running`."""
    held = _rec_path(spool, job_id, "held")
    running = _rec_path(spool, job_id, "running")
    try:
        os.rename(held, running)   # ATOMIC single-flight; the loser raises and bails
    except OSError:
        return None
    try:
        with open(running) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    rec["state"] = "running"
    rec["owner_pid"] = os.getpid()   # advisory ONLY; crash recovery decides from the file
    _atomic_write(running, rec)
    return rec


def writeback(spool, rec, outcome):
    """Resolve a `running` record by its run outcome. `outcome == "done"` clears it (terminal); any
    other outcome is a failure cause: bump `attempts`, recompute state via `next_state` (G4), set the
    backoff floor, and re-file under the next state's suffix. Returns the resolved state."""
    running = _rec_path(spool, rec["id"], "running")
    if outcome == "done":
        _unlink(running)
        _unlink(_snapshot_path(spool, rec["id"]))
        return "done"
    rec["attempts"] = rec.get("attempts", 0) + 1
    rec["last_error"] = outcome
    age = time.time() - rec.get("created", time.time())
    state = next_state(rec["attempts"], rec["last_error"], age)
    _unlink(running)
    if state == "expired":
        _unlink(_snapshot_path(spool, rec["id"]))   # ages out visibly + honestly; nothing lingers
        return "expired"
    rec["next_retry_after"] = time.time() + retry_backoff_s(rec["attempts"])
    rec["state"] = state
    _atomic_write(_rec_path(spool, rec["id"], "review" if state == "needs-review" else "held"), rec)
    return state


def recover_crashed(spool):
    """With the drainer flock held, every `*.running.json` is provably an orphan (a prior fire died
    mid-run) → return it to `held` (attempts++, cause 'preempted', owner_pid=None). Decides from the
    FILE, never the PID. Returns [(id, resolved_state), …]."""
    out = []
    for p in glob.glob(os.path.join(spool, "*.running.json")):
        try:
            with open(p) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            _unlink(p)
            continue
        rec["attempts"] = rec.get("attempts", 0) + 1
        rec["last_error"] = "preempted"
        rec["owner_pid"] = None
        age = time.time() - rec.get("created", time.time())
        state = next_state(rec["attempts"], rec["last_error"], age)
        _unlink(p)
        if state == "expired":
            _unlink(_snapshot_path(spool, rec["id"]))
            out.append((rec["id"], "expired"))
            continue
        rec["next_retry_after"] = time.time() + retry_backoff_s(rec["attempts"])
        rec["state"] = state
        _atomic_write(_rec_path(spool, rec["id"], "review" if state == "needs-review" else "held"), rec)
        out.append((rec["id"], state))
    return out


def expire(spool, job_id):
    """Terminal removal of a record in any state, plus its snapshot. Idempotent."""
    for st in _STATES:
        _unlink(_rec_path(spool, job_id, st))
    _unlink(_snapshot_path(spool, job_id))


def needs_review_items(spool):
    """The `needs-review` records, shaped for the G1 `review.json` sidecar ({id,title,since}). The
    drainer recomputes the sidecar from THIS authoritative set every tick (never a cached scalar)."""
    out = []
    for p in glob.glob(os.path.join(spool, "*.review.json")):
        try:
            with open(p) as f:
                r = json.load(f)
        except (OSError, ValueError):
            continue
        out.append({"id": r.get("id"), "title": r.get("title", ""), "since": r.get("created", 0.0)})
    return out
