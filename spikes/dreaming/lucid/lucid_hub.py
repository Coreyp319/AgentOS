#!/usr/bin/env python3
"""Lucid review hub — the "act" surface DATA LAYER for the held + needs-review durable queue
(ADR-0019 §2 "edge ownership": the model proposes nothing here; the HUMAN disposes). The drainer
(lucid_drain.py) re-runs held requests on a Tier::BestEffort lease and escalates the ones it can't —
this module is the read-only board the :8765 Lucid page renders, plus the three deterministic human
actions (retry / dismiss / approve) the page POSTs.

SCOPE — DURABLE (NON-PRIVATE) SPOOL ONLY. The ephemeral PRIVATE queue (lucid_priv_queue.py) has NO
review surface BY DESIGN: a private request is "never shown elsewhere" (ADR-0016), so it must never
appear on a hub-ish board and must never be acted on here. This module imports lucid_queue (which
physically refuses `private=True`, Condition 2) and never touches the private spool. There is no
parameter, no code path, that reaches a private item.

DETERMINISTIC + ATOMIC + ID-VALIDATED. Every action is a pure file transition driven by the user's
click — no model, no clock-as-ranking, no priority. Every transition is atomic (os.rename, or
lucid_queue's `_atomic_write` fsync+replace helper — never reimplemented here) and every id is
validated through lucid_queue's `_rec_path` / `_snapshot_path`, which run `_ID_RE` and join INSIDE the
spool dir, so a traversing id (`../`, absolute path, `a/b`) raises ValueError before any fs touch — an
id can never escape the spool.

NO FILESYSTEM PATHS REACH THE PAGE. `board()`'s held view strips `snapshot` and never emits the spool
path; it exposes `id`/`title`/`created`/`age_s` (a computed "asked N ago"), not the on-disk location.
`needs_review_items` is already path-free ({id,title,since}). The glance board (`lucid_jobs.recent`)
is passed through verbatim — it is the established page contract (a `clip`/`frame` field there is a
streamable media handle the page already serves via /api/clip, not a spool location).

────────────────────────────────────────────────────────────────────────────────────────────────────
ROUTES TO ADD TO lucid_web.py (a trivial paste when that file is quiet — DO NOT edit it from here).
One read-only GET, three state-changing POSTs. The POSTs reuse the EXACT existing CSRF + Origin gate
already in `do_POST` (the per-process `X-Lucid-Token` header; a cross-origin page can't read it):

  # in do_GET, beside /api/state:
  if path == "/api/queue":                         # read-only board (held + needs-review + glance)
      import lucid_hub as H
      return self._send(200, json.dumps(H.board()), "application/json")

  # in do_POST, add to the allow-list and dispatch. CSRF/Origin are already enforced above for
  # every path in this method, so these inherit the same fail-closed token check — no new gate:
  if path not in ("/api/dream", "/api/burn", "/api/start", "/api/delete",
                  "/api/queue/retry", "/api/queue/dismiss", "/api/queue/approve"):
      return self._send(404, "not found", "text/plain")
  ...
  if path in ("/api/queue/retry", "/api/queue/dismiss", "/api/queue/approve"):
      import lucid_hub as H
      jid = req.get("id")
      action = {"/api/queue/retry": H.retry,
                "/api/queue/dismiss": H.dismiss,
                "/api/queue/approve": H.approve}[path]
      try:
          ok = action(jid)                         # id-validated inside; a bad id -> ValueError
      except ValueError:
          return self._send(400, json.dumps({"error": "bad id"}), "application/json")
      return self._send(200, json.dumps({"ok": bool(ok)}), "application/json")

  Method / path / CSRF summary:
    GET  /api/queue          no CSRF (read-only)
    POST /api/queue/retry    X-Lucid-Token header REQUIRED   body {"id": "<job_id>"}
    POST /api/queue/dismiss  X-Lucid-Token header REQUIRED   body {"id": "<job_id>"}
    POST /api/queue/approve  X-Lucid-Token header REQUIRED   body {"id": "<job_id>"}
────────────────────────────────────────────────────────────────────────────────────────────────────
"""
import json
import os
import time

import lucid_jobs as J
import lucid_queue as Q


def _spool(spool):
    return Q.durable_dir() if spool is None else spool


def _held_view(rec, now):
    """A held record shaped for display. Exposes id / title / created / a computed `age_s` ("asked N
    ago"). DELIBERATELY drops `snapshot` and emits NO filesystem path (the spool location never
    reaches the page). `attempts` / `last_error` are kept (honest: "tried 3×, last: gpu-busy")."""
    created = rec.get("created", now)
    return {
        "id": rec.get("id"),
        "title": rec.get("title", ""),
        "created": created,
        "age_s": max(0, int(now - created)),     # "asked N ago" — derived, not a raw path/clock leak
        "attempts": rec.get("attempts", 0),
        "last_error": rec.get("last_error"),
    }


def board(spool=None):
    """Assemble the read-only "act" surface view. Cheap (a handful of small files), safe to back a
    `GET /api/queue` route. Returns a JSON-able dict:

        {"held":         [ {id,title,created,age_s,attempts,last_error}, … ],   # deferred, will re-run
         "needs_review": [ {id,title,since}, … ],                              # waiting on the human
         "recent":       [ … lucid_jobs.recent() … ]}                          # the glance board

    NO snapshot / filesystem path appears in `held` or `needs_review`. `recent` is the existing
    tmpfs glance contract, passed through verbatim. Durable spool only; never the private spool."""
    spool = _spool(spool)
    now = time.time()
    held = sorted(Q.read_held(spool), key=lambda r: r.get("seq", 0))   # arrival order; never a rank
    return {
        "held": [_held_view(r, now) for r in held],
        "needs_review": Q.needs_review_items(spool),   # already {id,title,since}; path-free by design
        "recent": J.recent(),                          # the glance board (non-private only by its own design)
    }


# ============================ the three deterministic human ACTIONS ============================
# Model proposes nothing; the human disposes (ADR-0019 §2). Each is a pure, atomic, id-validated file
# transition triggered by a click. None ranks, none touches the private spool.

def retry(job_id, spool=None):
    """Make a HELD item immediately eligible: clear its backoff floor (`next_retry_after` = 0) so the
    next drainer fire runs it now instead of after the exponential wait. Operates ONLY on a
    `<id>.held.json` record — a no-op returning False if the id isn't currently held (running / review
    / absent). Atomic via lucid_queue's `_atomic_write`; id validated by `_rec_path` (raises on a
    traversing id before any fs touch). Returns True iff a held record was made eligible."""
    spool = _spool(spool)
    held_path = Q._rec_path(spool, job_id, "held")   # validates id; joins inside the spool dir
    try:
        with open(held_path) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return False                                 # not held (or unreadable) -> no-op
    rec["next_retry_after"] = 0.0                    # eligibility floor cleared; NOT a priority bump
    Q._atomic_write(held_path, rec)                  # fsync + os.replace — never a half-written record
    return True


def dismiss(job_id, spool=None):
    """The user CANCELS the request. Terminal removal of the record + its snapshot in ANY state, via
    `lucid_queue.expire` (idempotent; unlinks `<id>.{held,running,review}.json` + `<id>.png`). Works
    on a held OR a needs-review item — the user can give up on either. id validated by `expire` ->
    `_rec_path` / `_snapshot_path`. Returns True (expire is idempotent; a no-op dismiss still 'succeeds')."""
    spool = _spool(spool)
    Q.expire(spool, job_id)   # raises ValueError on a bad/traversing id; else removes record + snapshot
    return True


def approve(job_id, spool=None):
    """The human APPROVES a NEEDS-REVIEW item to run: move `<id>.review.json` → `<id>.held.json`, clear
    `last_error`, and reset `next_retry_after` = 0 so the drainer re-runs it on the next fire. Atomic
    via `os.rename` of the marker + `_atomic_write` of the cleared record. Returns True iff a review
    record was promoted; a no-op returning False on a non-review id (held / running / absent).

    SAFETY INVARIANT — approve can NEVER resurrect a possible-minor:
      A `.review.json` record can, by construction, only carry a HUMAN cause — `b2-cant-verify` or
      `consent-borderline` (lucid_queue.HUMAN_ERRORS, the only `last_error`s `next_state` routes to
      "needs-review"). A POSSIBLE-MINOR is TERMINAL at the B2 gate (lucid_b2): such a seed is REFUSED
      at enqueue and NEVER becomes a queue record at all, so no `.review.json` for it can exist. This
      function only ever acts on a file whose suffix is `.review.json`; it therefore physically cannot
      act on a possible-minor. (Belt-and-suspenders: we also refuse to promote a record whose
      `last_error` is not in HUMAN_ERRORS — a record that should not have been a review item in the
      first place is left untouched rather than waved through.)"""
    spool = _spool(spool)
    review_path = Q._rec_path(spool, job_id, "review")   # validates id; joins inside the spool dir
    try:
        with open(review_path) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return False                                     # not a review item -> no-op (False)
    # Defense in depth: a review record can only have a human cause; refuse anything else outright.
    if rec.get("last_error") not in Q.HUMAN_ERRORS:
        return False
    rec["state"] = "held"
    rec["last_error"] = None                             # the human cleared the human concern
    rec["next_retry_after"] = 0.0                        # run on the next drainer fire
    held_path = Q._rec_path(spool, job_id, "held")
    # Promote the marker first (atomic single rename = the review->held transition), then stamp the
    # cleared fields atomically. The rename IS the state change; a crash between leaves a held record
    # the drainer will simply re-run (fail-safe), never a lost or duplicated request.
    os.rename(review_path, held_path)
    Q._atomic_write(held_path, rec)
    return True
