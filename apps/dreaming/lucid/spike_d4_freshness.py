#!/usr/bin/env python3
"""ADR-0036 D4 / I10 SPIKE — the live-beat FRESHNESS PREDICATE (the score-capping Phase-2 unknown).

Phase 2 lets a LIVE dream beat that couldn't run now be saved (on tab-close) as a durable needs-review
row, then replayed on "Make it". Unlike create-from-image (whose drainer RESETS the chain via L.start,
so a re-run can't duplicate), a live beat APPENDS onto an existing spine — so replaying it minutes/hours
later, after the dream has moved on, would graft a clip onto a story the user left. The determinism
review (I10) made this a Blocker: a deferred live beat is only replayable if its narrative closure is
frozen to VALUES at save time AND a freshness predicate refuses replay against a moved spine — declining
DETERMINISTICALLY (a `context-moved` needs-review terminal), never silently appending.

THIS SPIKE PROVES THE PREDICATE — pure logic, no GPU/lease/ComfyUI. It models the real chain-node shape
(`{id, parent, label, prompt, seed, clip, out_frame}`, per lucid_linear.start) and resolves a frame's
bytes through an injected `read_frame` (in production: `lucid_store.frame_abs` → open). The two functions
(`freeze_beat_intent`, `freshness_verdict`) are written to lift straight into Phase 2.

Run: python3 spike_d4_freshness.py
"""
import hashlib
import os
import sys
import tempfile

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


# ============================ the predicate (lift-ready) ============================
def _frame_hash(frame_bytes):
    """A content hash of a frame's bytes. blake2b-128 is plenty to detect a re-rendered frame; it is a
    CHANGE detector, not a security MAC, so a short digest keeps the record small."""
    return hashlib.blake2b(frame_bytes, digest_size=16).hexdigest()


def _find(chain, node_id):
    for n in chain.get("nodes", []):
        if n.get("id") == node_id:
            return n
    return None


def _child_count(chain, node_id):
    return sum(1 for n in chain.get("nodes", []) if n.get("parent") == node_id)


def freeze_beat_intent(chain, parent_id, prompt_final, *, rating, seed,
                       quality="draft", length=None, read_frame):
    """Freeze a LIVE dream beat's narrative closure to VALUES at the moment the user commits it (the
    tab-close → save-as-needs-review edge). Captures the parent node id, a content hash of the parent's
    CURRENT out_frame (the frame the beat animates from), the gated `prompt_final`, the `rating` floor,
    the base `seed`, `quality`, `length`, and the parent's child-count at freeze time (so a later
    branch off the same parent is detectable). `read_frame(out_frame) -> bytes` resolves the frame
    (production: lucid_store.frame_abs + open). Raises if the parent is already gone at freeze."""
    node = _find(chain, parent_id)
    if node is None:
        raise ValueError(f"cannot freeze: parent {parent_id!r} not in the chain")
    fh = _frame_hash(read_frame(node["out_frame"])) if node.get("out_frame") else None
    return {
        "v": 1,
        "parent_id": parent_id,
        "parent_frame_hash": fh,
        "parent_children_at_freeze": _child_count(chain, parent_id),
        "prompt_final": prompt_final,
        "rating": rating,
        "seed": seed,
        "quality": quality,
        "length": length,
    }


def freshness_verdict(frozen, chain, *, read_frame, current_rating=None, branch_is_stale=True):
    """ADR-0036 D4 / I10: is a frozen live-beat intent still safe to replay against the CURRENT chain?
    Returns ("clean", None) iff the narrative closure is intact, else ("context-moved", <reason>).

    HARD triggers (always decline — the spine genuinely moved):
      * parent-gone     — the node the beat branched from is no longer in the chain;
      * frame-changed   — the parent's out_frame was re-rendered (hash mismatch) since freeze;
      * rating-moved    — the dream's content-rating floor changed (the beat was composed under another).
    CONSERVATIVE trigger (knob `branch_is_stale`, default ON — the v1 choice the council asked for):
      * spine-branched  — the parent gained a NEW child since freeze, i.e. the dream advanced past this
                          point while the beat sat in the queue. A deferred live beat means "what happens
                          next FROM HERE"; if the user already branched here, the held beat is stale intent.

    Declines are DETERMINISTIC and explained — never a silent append. Replay is permitted ONLY when every
    check passes, so the irreversible "Make it" can never graft a clip onto a story the user left."""
    node = _find(chain, frozen["parent_id"])
    if node is None:
        return "context-moved", "parent-gone"
    cur = _frame_hash(read_frame(node["out_frame"])) if node.get("out_frame") else None
    if cur != frozen.get("parent_frame_hash"):
        return "context-moved", "frame-changed"
    if current_rating is not None and current_rating != frozen.get("rating"):
        return "context-moved", "rating-moved"
    if branch_is_stale and _child_count(chain, frozen["parent_id"]) > frozen.get("parent_children_at_freeze", 0):
        return "context-moved", "spine-branched"
    return "clean", None


# ============================ the proof ============================
# A frame store: a real file per frame name in a tmpdir, so read_frame reads actual bytes (faithful to
# production, where ST.frame_abs resolves to an on-disk PNG the drain hashes — never a URL re-fetch).
TMP = tempfile.mkdtemp(prefix="d4spike-")


def _write_frame(name, content):
    p = os.path.join(TMP, name)
    with open(p, "wb") as f:
        f.write(content)
    return name


def _read_frame(name):
    with open(os.path.join(TMP, name), "rb") as f:
        return f.read()


# A small dream: opening -> beatA (the spine tip the user branches a new beat from).
_write_frame("n0.png", b"OPENING-FRAME-BYTES")
_write_frame("a.png", b"BEAT-A-FRAME-BYTES-v1")
chain = {"session": "dream-x", "private": False, "seed": 12345, "nodes": [
    {"id": 0, "parent": None, "label": "opening", "prompt": None, "seed": None, "clip": "c0.mp4", "out_frame": "n0.png"},
    {"id": 1, "parent": 0, "label": "a", "prompt": "a quiet hallway", "seed": 111, "clip": "c1.mp4", "out_frame": "a.png"},
]}

# The user commits a beat that branches off node 1 (the current tip).
frozen = freeze_beat_intent(chain, 1, "the door swings open", rating="sfw", seed=222,
                            read_frame=_read_frame)
check("freeze captures the parent id", frozen["parent_id"] == 1)
check("freeze captures a parent-frame hash", isinstance(frozen["parent_frame_hash"], str) and len(frozen["parent_frame_hash"]) == 32)
check("freeze captures the gated prompt + seed + rating", frozen["prompt_final"] == "the door swings open" and frozen["seed"] == 222 and frozen["rating"] == "sfw")
check("freeze records the parent child-count (0 at freeze)", frozen["parent_children_at_freeze"] == 0)

# (1) CLEAN — the spine is exactly as it was at freeze → replay is permitted.
v, why = freshness_verdict(frozen, chain, read_frame=_read_frame, current_rating="sfw")
check("CLEAN: intact spine -> replay permitted", v == "clean" and why is None)

# (2) FRAME-CHANGED — the parent (node 1) was re-rendered (hero pass / refine) since freeze.
_write_frame("a.png", b"BEAT-A-FRAME-BYTES-v2-REDONE")     # same name, new bytes
v, why = freshness_verdict(frozen, chain, read_frame=_read_frame, current_rating="sfw")
check("CONTEXT-MOVED: parent frame re-rendered -> frame-changed", v == "context-moved" and why == "frame-changed")
_write_frame("a.png", b"BEAT-A-FRAME-BYTES-v1")            # restore for the remaining cases

# (3) PARENT-GONE — the dream was edited and node 1 deleted.
pruned = {"session": "dream-x", "private": False, "seed": 12345, "nodes": [chain["nodes"][0]]}
v, why = freshness_verdict(frozen, pruned, read_frame=_read_frame, current_rating="sfw")
check("CONTEXT-MOVED: parent deleted -> parent-gone", v == "context-moved" and why == "parent-gone")

# (4) RATING-MOVED — the dream's content floor changed after the beat was composed.
v, why = freshness_verdict(frozen, chain, read_frame=_read_frame, current_rating="mature")
check("CONTEXT-MOVED: rating floor changed -> rating-moved", v == "context-moved" and why == "rating-moved")

# (5) SPINE-BRANCHED — while the beat sat in the queue the user branched a NEW child off node 1.
_write_frame("b.png", b"BEAT-B-FRAME-BYTES")
branched = {"session": "dream-x", "private": False, "seed": 12345, "nodes": chain["nodes"] + [
    {"id": 2, "parent": 1, "label": "b", "prompt": "a sudden gust", "seed": 333, "clip": "c2.mp4", "out_frame": "b.png"},
]}
v, why = freshness_verdict(frozen, branched, read_frame=_read_frame, current_rating="sfw")
check("CONTEXT-MOVED: dream advanced past this point -> spine-branched", v == "context-moved" and why == "spine-branched")
# ...and the conservative branch trigger is a KNOB: with it off, a sibling branch is allowed.
v, why = freshness_verdict(frozen, branched, read_frame=_read_frame, current_rating="sfw", branch_is_stale=False)
check("knob off: a sibling branch alone is tolerated (frame still matches)", v == "clean")

# (6) Determinism: the verdict is a pure function of (frozen, chain, frame-bytes) — same inputs, same
# verdict, every time (no clock, no randomness, no model). Run it twice and compare.
a = freshness_verdict(frozen, chain, read_frame=_read_frame, current_rating="sfw")
b = freshness_verdict(frozen, chain, read_frame=_read_frame, current_rating="sfw")
check("DETERMINISTIC: identical inputs -> identical verdict", a == b)

# (7) The hash is the same the production drain would compute (blake2b-128 over the exact frame bytes),
# so a freeze on box A and a verdict on box B agree — the record is portable, not process-bound.
check("PORTABLE: parent_frame_hash == blake2b-128(frame bytes)",
      frozen["parent_frame_hash"] == hashlib.blake2b(_read_frame("a.png"), digest_size=16).hexdigest())

import shutil
shutil.rmtree(TMP, ignore_errors=True)

print(f"{ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
