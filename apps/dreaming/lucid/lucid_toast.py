#!/usr/bin/env python3
"""Lucid recovery-toast renderer (ADR-0019 §5, G5 — the HARD gate before Phase 2).

WHAT THIS IS: a *view* over an already-durable held/needs-review row. It builds the
`notify-send` argv that swaync renders, and (thinly) sends it. It is the accelerator,
NOT the source of truth. The durable spool (`lucid_queue.py`) is the backstop; this
module never writes, mutates, or even imports queue state. A toast that is never seen,
times out, is swiped, or hits a swaync-down desktop is a NO-OP on the held row — the
drainer auto-runs the request regardless. That is the whole point of G5: the toast must
never become the new silent-drop.

PERSIST-FIRST / TOAST-SECOND is the load-bearing invariant. The callers (the deferral
sites in `create_from_image.py`/`lucid_drain.py`) MUST `enqueue()`+fsync the row BEFORE
calling any `notify_*` here. We make that structural in two ways:
  1. `notify_held`/`notify_review` REQUIRE the durable record (a dict carrying `id` +
     `state`) — there is no overload that takes a bare title, so you cannot render a
     held/review toast without a row that already exists.
  2. A guard (`_require_durable_record`) raises if handed a record that is missing its
     `id`, is not in a persisted state, or is flagged `private` — so a private/anonymous
     request physically cannot be rendered as an actionable, durable-backed toast.

If `enqueue()` itself raised at the call site, the caller must NOT call `notify_held`;
it should call `notify_enqueue_failed()` — a no-action `critical` toast that tells the
user to re-trigger. NEVER an action button backed by a row that does not exist
(fail-open honesty, mirroring `feed.rs` UNKNOWN, not the calm `idle`).
"""
import shutil
import subprocess
import sys
import time

APP = "AgentOS · Create"
ICON = "camera-video"

# ---- action keys (carry the job_id so a restarted broker resolves the job from the key alone) ----
# The G3 drainer-folded broker subscribes to ActionInvoked(id, action_key) and splits on ':'.
ACTION_RUN = "run"        # -> run:<job_id>  : stays held, intent_confirmed; drainer runs when free
ACTION_CANCEL = "cancel"  # -> cancel:<job_id>: the ONLY path that removes the row (held -> expired)

# Persisted states this view is allowed to render an actionable, durable-backed toast for.
_DURABLE_STATES = ("held", "needs-review")


# ============================================================================
# G5_CONTRACT — the written hand-off so a UI implementer can wire swaync to this.
# ============================================================================
G5_CONTRACT = """\
G5 — recovery-toast a11y + persistence-on-dismiss (ADR-0019 §5)

ORDERING INVARIANT (the spine, non-negotiable):
  persist the held row FIRST (durable spool, fsync) -> THEN show the toast.
  The toast is a pure accelerator over an already-safe state. This module renders
  ONLY; it never writes queue state. Consequence: every toast lifecycle event is a
  no-op on the row (see table). If enqueue() raised, call notify_enqueue_failed()
  (a no-action critical toast) -- NEVER an action backed by a row that doesn't exist.

PERSISTENCE-ON-DISMISS TABLE (the airtight part — the forbidden cell does not exist):
  Action / event              | Held row outcome
  ----------------------------|---------------------------------------------
  "Run when free" invoked     | stays held, intent_confirmed=true (drainer runs it)
  "Cancel" invoked            | held -> expired (visible retraction, count decrements)
  toast timeout               | NO-OP on row (drainer still auto-runs)
  swipe / Esc / close (X)     | NO-OP on row (this is DISMISS, not Cancel)
  never seen / swaync down    | NO-OP on row (drainer still auto-runs)
  --------------------------------------------------------------------------------
  DISMISS != CANCEL. Closing the toast leaves the request held. Only the explicit
  "Cancel" action removes it. Their labels are distinct on purpose:
    close  SR label: "Dismiss this notice (the request stays held)"
    cancel SR label: "Cancel this held video request"

NO COUNTDOWN / no urgency dark-pattern (SC 2.2.1):
  There is NO visible timer and NO deadline. The held row is patient — it reads
  "asked N min ago", not "expires in N". The backstop (the durable row + drainer),
  not a timer, satisfies "enough time". Urgency is 'normal'/'low', never 'critical'
  for a held toast (critical is reserved for the no-row enqueue-failure case).

WCAG 2.2 a11y SLOTS:
  - keyboard-operable actions, mouse-unplugged (SC 2.1.1)
  - focus order Run -> Cancel, Run takes initial focus (SC 2.4.3)
  - visible non-color focus ring >=3:1, not clipped (SC 2.4.7 / 2.4.11) [swaync style]
  - self-sufficient SR labels carried in the action LABEL text (SC 4.1.2):
      "Run this held video when the graphics card is free" / "Cancel this held video request"
  - polite-not-assertive announce: a deferral is calm weather (urgency normal/low,
    category transfer for the held lane, never a 'critical' alert)
  - >=24x24px targets (SC 2.5.8) [swaync style]
  - prefers-reduced-motion honored (SC 2.3.3) [swaync style]
  The toast surface is an opaque swaync card, so contrast is style-independent and the
  held toast never borrows the warm palette (warm-hue invariant).

PRIVATE CARVE-OUT (binding):
  A private deferral toast carries ZERO action buttons and NO link-out / NO durable-row
  reference. It is informational only and transient (it points at nothing persisted,
  because nothing IS persisted — the private lane is RAM/tmpfs, burned on logout).
  `private=True` therefore takes only a title (never a record), emits no -A actions,
  no replace-hint job id, no category that implies a board, and a transient hint.
"""


# ============================================================================
# guards — make persist-first structural
# ============================================================================
def _require_durable_record(rec):
    """Raise unless `rec` is an already-persisted, NON-private, durable row. This is how
    persist-first is enforced in code: you cannot render an actionable held/review toast
    without the row that the drainer will actually run. A bare title is not accepted; a
    `private=True` record is rejected outright (it has no durable surface to point at)."""
    if not isinstance(rec, dict):
        raise TypeError("notify_held/notify_review need the DURABLE queue record "
                        "(persist-first: the row exists BEFORE the toast)")
    job_id = rec.get("id")
    if not job_id:
        raise ValueError("toast refused: record has no durable id — the toast is a VIEW over an "
                         "already-persisted row; render nothing if the row does not exist")
    if rec.get("private"):
        raise ValueError("toast refused: a private record has NO durable surface — use the "
                         "private=True informational carve-out, not an actionable held toast")
    state = rec.get("state")
    if state not in _DURABLE_STATES:
        raise ValueError(f"toast refused: state {state!r} is not a persisted durable state "
                         f"{_DURABLE_STATES} — render only over a held/needs-review row")
    return job_id


def _asked_ago(created):
    """Patient, NON-deadline relative time for the body ('asked N min ago'). NEVER a
    countdown — there is no expiry shown to the user (anti-dark-pattern, SC 2.2.1)."""
    try:
        secs = max(0, int(time.time() - float(created)))
    except (TypeError, ValueError):
        return "just now"
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"asked {mins} min ago"
    hrs = mins // 60
    return f"asked {hrs} hr ago"


def _replace_hint(job_id):
    """Stable per-job synchronous hint so a re-deferral of the SAME job REPLACES rather
    than stacks (one row -> one toast). Synchronous hint also means 'transient, do not
    persist in the notification center as a separate item' on swaync."""
    return f"string:x-canonical-private-synchronous:lucid-{job_id}"


# ============================================================================
# pure argv builders (unit-testable; no shell, no I/O)
# ============================================================================
def _build_held_argv(rec, *, kind="held"):
    """PURE. Build the `notify-send` argv for an already-persisted held/needs-review row.
    argv-only (no shell) — the only interpolated value is the validated job_id from the
    durable record, carried in the action KEYS so the broker resolves the job from the key.

    Actions (focus order Run -> Cancel, Run first => listed first):
      -A run:<id>=<SR label>     keyboard-focusable, self-sufficient screen-reader label
      -A cancel:<id>=<SR label>  the ONLY remove path; label distinct from the close (X)
    There is NO -A for close: swaync's built-in dismiss (X / swipe / Esc) is a no-op on
    the row by construction (it invokes no action key). Its SR text is set via the hint
    so 'close' reads "Dismiss this notice (the request stays held)" — dismiss != cancel.
    """
    job_id = _require_durable_record(rec)
    title = str(rec.get("title") or "Held video request")[:80]
    ago = _asked_ago(rec.get("created"))

    if kind == "needs-review":
        summary = "A held video needs your review"
        body = (f"This request is held and waiting for you ({ago}). It stays held until you "
                f"act — nothing is lost.")
        urgency = "normal"
        category = "transfer.complete"
    else:  # held (the common GPU-busy deferral)
        summary = "Held — Lucid will run this when the graphics card is free"
        body = (f"Your request is held and runs automatically when the graphics card is free "
                f"({ago}). You never have to ask again. Closing this notice keeps it held.")
        urgency = "normal"          # calm weather — NEVER 'critical' for a held toast
        category = "transfer"

    argv = [
        "notify-send",
        "-a", APP,
        "-i", ICON,
        "-u", urgency,              # polite/calm, not assertive (a11y: not an alarm)
        "-c", category,
        # Replace-on-re-defer + transient (do not pile a per-defer item in the center):
        "-h", _replace_hint(job_id),
        # SR text for the built-in close affordance: dismiss is explicitly NOT cancel.
        "-h", "string:x-agentos-close-label:Dismiss this notice (the request stays held)",
        # Focusable, screen-reader-self-sufficient actions. Run is FIRST = initial focus.
        "-A", f"{ACTION_RUN}:{job_id}=Run this held video when the graphics card is free",
        "-A", f"{ACTION_CANCEL}:{job_id}=Cancel this held video request",
        summary,
        body,
    ]
    return argv


def _build_ready_argv(rec):
    """PURE. A 'your held video is ready' toast over a (still durable) row. Carries a
    single non-removing action that opens Lucid; the row's removal is the drainer's job
    on a confirmed 'done', never a toast lifecycle event."""
    job_id = _require_durable_record(rec)
    argv = [
        "notify-send",
        "-a", APP,
        "-i", ICON,
        "-u", "normal",
        "-c", "transfer.complete",
        "-h", _replace_hint(job_id),
        "-A", f"open:{job_id}=Open Lucid to view your video",
        "Your held video is ready",
        "Saved to your dreams — open Lucid to view it.",
    ]
    return argv


def _build_private_argv(title):
    """PURE. The PRIVATE carve-out. ZERO actions, NO durable-row reference, NO replace
    hint job id, NO board-implying category, and a TRANSIENT hint (RAM-only, burned on
    logout). Takes only a title — it CANNOT take a record, so it can never point at a
    persisted surface. Urgency 'low': informational, calm."""
    summary = "Private: held in memory"
    body = (f"{str(title or 'Your private request')[:80]} — held in memory, retrying on its own "
            f"this session; gone when you log out.")
    argv = [
        "notify-send",
        "-a", APP,
        "-i", ICON,
        "-u", "low",                       # informational, not an alert
        # transient: never persists in the notification center; no synchronous JOB id.
        "-h", "string:x-canonical-private-synchronous:lucid-private",
        "-h", "int:transient:1",
        summary,
        body,
        # NOTE: no -A actions, by contract. A private hold has no durable surface to act on.
    ]
    return argv


def _build_enqueue_failed_argv():
    """PURE. The fail-open honesty toast for when the row could NOT be persisted. NO action
    button (there is no row to back it), `critical` so it is actually seen, and it tells the
    user to re-trigger. This is the ONLY honest critical toast in this module."""
    return [
        "notify-send",
        "-a", APP,
        "-i", ICON,
        "-u", "critical",
        "Couldn't hold your request",
        "Something went wrong saving it, so it wasn't held. Please trigger 'Create video' "
        "again — nothing was lost, but it won't retry on its own.",
    ]


# ============================================================================
# thin send wrappers (the only impure part; skipped in tests)
# ============================================================================
def _send(argv):
    """Fire-and-forget the built argv. argv-only (no shell=True). Falls open to stderr if
    notify-send is missing — a toast failure is NEVER allowed to affect the held row."""
    if shutil.which("notify-send") is None:
        print(f"[lucid_toast] notify-send unavailable: {argv[-2]} — {argv[-1]}", file=sys.stderr)
        return False
    try:
        subprocess.Popen(argv)   # detached; we do not block the drainer/launcher
        return True
    except Exception as e:
        print(f"[lucid_toast] toast send failed ({e}) — the held row is unaffected", file=sys.stderr)
        return False


# ============================================================================
# public API
# ============================================================================
def notify_held(rec):
    """Render the recovery toast for an ALREADY-PERSISTED held row. `rec` is the durable
    record from `lucid_queue.enqueue()` (carrying `id`, `title`, `created`, `state`).
    Persist-first: the row MUST already be fsynced before this is called. Returns the sent
    argv (for logging/testing). Dismissing the toast is a no-op on `rec`."""
    return _build_held_argv(rec, kind="held"), _send(_build_held_argv(rec, kind="held"))


def notify_review(rec):
    """Render the toast for an already-persisted needs-review row (escalated to a human).
    Same persist-first / dismiss!=cancel contract as `notify_held`."""
    return _build_held_argv(rec, kind="needs-review"), _send(_build_held_argv(rec, kind="needs-review"))


def notify_ready(rec):
    """Render the 'your held video is ready' toast over the (still durable) row."""
    return _build_ready_argv(rec), _send(_build_ready_argv(rec))


def notify_private(title):
    """The PRIVATE carve-out: an action-less, transient, durable-reference-free toast.
    Takes ONLY a title — structurally cannot point at a persisted row."""
    return _build_private_argv(title), _send(_build_private_argv(title))


def notify_enqueue_failed():
    """Fail-open honesty: the row could NOT be persisted, so show a no-action critical
    toast telling the user to re-trigger. NEVER an action backed by a non-existent row."""
    return _build_enqueue_failed_argv(), _send(_build_enqueue_failed_argv())


if __name__ == "__main__":
    # Tiny manual smoke: print the contract + a sample argv. Does not send.
    print(G5_CONTRACT)
    sample = {"id": "shot_demo", "title": "Create from image", "created": time.time() - 240,
              "state": "held", "private": False}
    print("\nheld argv:", _build_held_argv(sample))
    print("\nprivate argv:", _build_private_argv("a private clip"))
