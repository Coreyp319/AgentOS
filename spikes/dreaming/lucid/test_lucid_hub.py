#!/usr/bin/env python3
"""Unit tests for lucid_hub (ADR-0019 §2) — the "act" surface data layer: the read-only board
assembler + the three deterministic human actions (retry / dismiss / approve) over the DURABLE queue.
Seeds the spool through the real chokepoint (lucid_queue.enqueue) and crafts a `.review.json` exactly
as the drainer does (writeback of a HUMAN-cause failure). No GPU/daemon/model. Run: python3 test_lucid_hub.py"""
import glob
import json
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="lucid_hub_test_")
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")
# lucid_jobs.recent() reads XDG_RUNTIME_DIR/agentos/lucid-jobs (tmpfs glance board) — sandbox it too.
os.environ["XDG_RUNTIME_DIR"] = os.path.join(_TMP, "run")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_queue as Q   # noqa: E402
import lucid_jobs as J    # noqa: E402
import lucid_hub as H     # noqa: E402

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


SPOOL = os.path.join(_TMP, "spool")


def _make_review(job_id, title, cause):
    """Craft a `<id>.review.json` the way the drainer does: enqueue → claim → writeback(human cause).
    `next_state` routes a HUMAN_ERRORS cause straight to needs-review (filed under .review.json)."""
    assert cause in Q.HUMAN_ERRORS
    Q.enqueue(job_id, title, spool=SPOOL)
    Q.claim(SPOOL, job_id)
    rec = json.load(open(os.path.join(SPOOL, f"{job_id}.running.json")))
    state = Q.writeback(SPOOL, rec, cause)
    assert state == "needs-review", state
    return os.path.join(SPOOL, f"{job_id}.review.json")


# a real PNG so enqueue snapshots it into the spool (we assert dismiss removes it)
png_src = os.path.join(_TMP, "seed.png")
open(png_src, "wb").write(b"PNGBYTES")

# ============================ board() shape + NO path leak ============================
Q.enqueue("held_a", "Sunset over hills", png_src, spool=SPOOL)
Q.enqueue("held_b", "Aurora drift", png_src, spool=SPOOL)
_make_review("rev_c", "Borderline consent clip", "consent-borderline")
# a glance-board entry (the tmpfs lucid_jobs surface)
J.create("Create from image", job_id="glance_x")

b = H.board(SPOOL)
check("board has held / needs_review / recent keys", set(b) == {"held", "needs_review", "recent"})
check("board.held lists the held records", sorted(h["id"] for h in b["held"]) == ["held_a", "held_b"])
check("board.held is in arrival (seq) order", [h["id"] for h in b["held"]] == ["held_a", "held_b"])
check("board.held shapes title + age_s", b["held"][0]["title"] == "Sunset over hills"
      and isinstance(b["held"][0]["age_s"], int) and b["held"][0]["age_s"] >= 0)
check("board.needs_review surfaces the review item ({id,title,since})",
      [r["id"] for r in b["needs_review"]] == ["rev_c"]
      and "since" in b["needs_review"][0] and b["needs_review"][0]["title"] == "Borderline consent clip")
check("board.recent is the glance board", any(j["id"] == "glance_x" for j in b["recent"]))

# the load-bearing privacy/safety property: NO snapshot or filesystem path reaches the page
_blob = json.dumps(b)
check("board exposes NO snapshot field", all("snapshot" not in h for h in b["held"]))
check("board leaks NO spool path string", SPOOL not in _blob and ".png" not in _blob and ".held.json" not in _blob)
check("board.held leaks no .png path in any value",
      all(not (isinstance(v, str) and v.endswith(".png")) for h in b["held"] for v in h.values()))

# ============================ retry — clears backoff, held-only ============================
# put held_b into a backed-off state: claim → fail (gpu-busy) → it returns to held with a future floor
Q.claim(SPOOL, "held_b")
Q.writeback(SPOOL, json.load(open(os.path.join(SPOOL, "held_b.running.json"))), "gpu-busy")
recb = json.load(open(os.path.join(SPOOL, "held_b.held.json")))
check("precondition: held_b has a future backoff floor", recb["next_retry_after"] > 0)
check("retry returns True on a held item", H.retry("held_b", SPOOL) is True)
check("retry cleared next_retry_after to 0 (immediately eligible)",
      json.load(open(os.path.join(SPOOL, "held_b.held.json")))["next_retry_after"] == 0)
check("retry preserved attempts (not a reset, just eligibility)",
      json.load(open(os.path.join(SPOOL, "held_b.held.json")))["attempts"] == 1)
check("retry is a no-op (False) on a needs-review id (only acts on held)", H.retry("rev_c", SPOOL) is False)
check("retry is a no-op (False) on an absent id", H.retry("nope", SPOOL) is False)

# ============================ dismiss — removes record + snapshot, any state ============================
check("precondition: held_a snapshot exists", os.path.isfile(os.path.join(SPOOL, "held_a.png")))
check("dismiss returns True", H.dismiss("held_a", SPOOL) is True)
check("dismiss removed the held record", not os.path.isfile(os.path.join(SPOOL, "held_a.held.json")))
check("dismiss removed the snapshot too", not os.path.isfile(os.path.join(SPOOL, "held_a.png")))
check("dismiss works on a needs-review item too", H.dismiss("rev_c", SPOOL) is True
      and not os.path.isfile(os.path.join(SPOOL, "rev_c.review.json")))
check("dismiss is idempotent (True again on an already-gone id)", H.dismiss("held_a", SPOOL) is True)

# ============================ approve — review → held, last_error cleared, review-only ============================
rev_path = _make_review("rev_d", "Cannot verify subject", "b2-cant-verify")
check("precondition: rev_d is a review record with a human cause",
      json.load(open(rev_path))["last_error"] == "b2-cant-verify")
check("approve returns True on a review item", H.approve("rev_d", SPOOL) is True)
check("approve moved review → held", not os.path.isfile(rev_path)
      and os.path.isfile(os.path.join(SPOOL, "rev_d.held.json")))
_recd = json.load(open(os.path.join(SPOOL, "rev_d.held.json")))
check("approve cleared last_error", _recd["last_error"] is None)
check("approve reset next_retry_after to 0 (drainer re-runs it next fire)", _recd["next_retry_after"] == 0)
check("approve set state back to held", _recd["state"] == "held")
check("approve is a no-op (False) on a non-review (held) id", H.approve("held_b", SPOOL) is False)
check("approve is a no-op (False) on an absent id", H.approve("ghost", SPOOL) is False)

# ============================ PROOF: approve can NEVER resurrect a possible-minor ============================
# A possible-minor is terminal at the B2 gate — it is REFUSED at enqueue and never becomes a record,
# so no `.review.json` for it can ever exist. We prove the structural property two ways:
# (1) "possible-minor" is not a HUMAN_ERRORS cause, so next_state never files it under .review.json;
check("possible-minor is NOT a review-eligible cause (gate-terminal, never escalated)",
      "possible-minor" not in Q.HUMAN_ERRORS
      and Q.next_state(1, "possible-minor", 10) != "needs-review")
# (2) even if a malformed review record somehow carried a non-human cause, approve's belt-and-suspenders
#     check refuses to promote it — it can ONLY wave through b2-cant-verify / consent-borderline.
poison = os.path.join(SPOOL, "poison.review.json")
Q._atomic_write(poison, {"id": "poison", "seq": 99, "created": 0.0, "title": "x",
                         "snapshot": None, "attempts": 1, "last_error": "possible-minor",
                         "next_retry_after": 0.0, "state": "needs-review"})
check("approve REFUSES a review record whose cause isn't a human cause (can't wave through a non-human-cause)",
      H.approve("poison", SPOOL) is False)
check("approve left the poison record untouched (not promoted to held)",
      os.path.isfile(poison) and not os.path.isfile(os.path.join(SPOOL, "poison.held.json")))

# ============================ id validation — no traversal escapes the spool ============================
def _rejects(fn, bad):
    try:
        fn(bad, SPOOL)
        return False
    except ValueError:
        return True

check("retry rejects a traversing id (../)", _rejects(H.retry, "../etc/passwd"))
check("dismiss rejects a traversing id (../)", _rejects(H.dismiss, "../../secret"))
check("approve rejects a traversing id (../)", _rejects(H.approve, "../boom"))
check("actions reject an absolute-path id", _rejects(H.dismiss, "/etc/shadow"))
check("actions reject a slash-bearing id", _rejects(H.retry, "a/b"))
# the rejection must happen BEFORE any fs touch — nothing outside the spool is created/removed
check("a rejected id created nothing in the spool",
      not glob.glob(os.path.join(SPOOL, "*passwd*"))
      and not glob.glob(os.path.join(SPOOL, "*secret*")))

# ============================ private spool is never imported/touched ============================
# The load-bearing property: lucid_hub never IMPORTS the private queue module (the docstring may
# *name* it descriptively to explain the scope boundary — that's documentation, not a code path).
import importlib  # noqa: E402
import sys as _sys  # noqa: E402
_before = set(_sys.modules)
importlib.reload(H)
_pulled_in = {m for m in (set(_sys.modules) - _before) if "priv" in m}
check("lucid_hub never imports the private queue (no priv module pulled in)", not _pulled_in)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lucid_hub.py")).read()
check("lucid_hub has no `import lucid_priv_queue` statement",
      "import lucid_priv_queue" not in src and "from lucid_priv_queue" not in src)

# --- cleanup ---
import shutil  # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)

print(f"lucid_hub: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
