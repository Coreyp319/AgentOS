#!/usr/bin/env python3
"""Lucid review sidecar — the SOLE writer of review.json (ADR-0019 §6, G1).

review.json is the one file the ambient feed (agentosd `feed.rs::read_lucid_review`) reads to
bloom the wallpaper warm for a LOCAL request awaiting human review (a B2 can't-verify, a borderline
consent). It is a DIFFERENT path from the Hermes needs-you plugin's ~/.hermes/needs_you.json, so the
two producers never name the same file — the "collision with the plugin's whole-set os.replace"
cannot occur (the G1 no-double-count invariant: disjoint intent sets, summed once in feed.rs).

Contract (must match feed.rs::LucidReviewFile):
  {"schema":1,"pending_review":N,"updated_at":<epoch float>,"items":[{"id","title","since"},...]}
  * pending_review counts `needs-review` items ONLY — NEVER held:deferred (held is calm/idle and
    must stay cool; the field name enforces it). It is RECOMPUTED from the item set on every write,
    never an incremented cached scalar (the drainer recomputes from the authoritative spool each tick).
  * updated_at is a liveness heartbeat the drainer rewrites EVERY tick (even when unchanged). feed.rs
    has its OWN 12s staleness gate, so the warm bloom retracts ~12s after the heartbeat stops even
    though the file lingers — and a local review still blooms while Hermes is down (fail-open).

Atomic temp+os.replace in the same dir (the lucid_jobs.py idiom) — a reader sees the old or the new
file, never a half-written one.
"""
import json
import os
import tempfile
import time

SCHEMA = 1


def _queue_dir():
    """`$XDG_DATA_HOME/agentos/lucid-queue` (or `~/.local/share/agentos/lucid-queue`). MUST match
    feed.rs::lucid_data_path so the sole writer and the reader name the same file."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "agentos", "lucid-queue")


def review_path():
    return os.path.join(_queue_dir(), "review.json")


def _write_doc(items, now):
    d = _queue_dir()
    os.makedirs(d, exist_ok=True)
    doc = {"schema": SCHEMA, "pending_review": len(items),
           "updated_at": round(now, 2), "items": list(items)}
    fd, tmp = tempfile.mkstemp(prefix=".review.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, review_path())   # atomic; readers see old-or-new, never partial
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return doc


def write(items, now=None):
    """Write review.json from the AUTHORITATIVE item set (each {id,title,since}). pending_review is
    recomputed as len(items) — never +=/-= a cached scalar."""
    return _write_doc(list(items), time.time() if now is None else now)


def heartbeat(now=None):
    """Rewrite updated_at without changing the item set — the liveness tick when nothing changed. Its
    ABSENCE (no heartbeat for > feed.rs STALE_SECS) is what retracts the bloom, so the drainer calls
    this every tick even when the queue is unchanged. Safe on a missing file (treated as empty)."""
    try:
        with open(review_path()) as f:
            items = json.load(f).get("items", [])
    except (OSError, ValueError):
        items = []
    return _write_doc(items, time.time() if now is None else now)


def clear(now=None):
    """No items awaiting review → write an empty-but-LIVE file so the bloom retracts promptly rather
    than waiting out staleness."""
    return _write_doc([], time.time() if now is None else now)
