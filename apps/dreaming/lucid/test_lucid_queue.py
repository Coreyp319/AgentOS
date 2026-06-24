#!/usr/bin/env python3
"""Unit tests for lucid_queue (ADR-0019 §5, G3/G4/G6) — the durable deferral spool. Covers the pure
retry policy (G4), the anti-scheduler drain order (G6), the enqueue chokepoint, the atomic claim +
single-flight, crash recovery from the FILE, and the lifecycle transitions. No GPU/daemon/model.
Run: python3 test_lucid_queue.py"""
import json
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="lucid_queue_test_")
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_queue as Q  # noqa: E402

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


SPOOL = os.path.join(_TMP, "spool")


def _held_files():
    import glob
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(SPOOL, "*.held.json")))


# ============================ G4 — pure retry policy ============================
check("next_state held while fresh + retries remain", Q.next_state(1, "gpu-busy", 10) == "held")
check("next_state needs-review when retries exhausted", Q.next_state(Q.MAX_ATTEMPTS, "gpu-busy", 10) == "needs-review")
check("next_state needs-review for a human cause immediately", Q.next_state(1, "b2-cant-verify", 10) == "needs-review")
check("next_state expired past the defer TTL", Q.next_state(1, "gpu-busy", Q.DEFER_TTL_S + 1) == "expired")
check("next_state human cause waits the longer review TTL", Q.next_state(1, "consent-borderline", Q.DEFER_TTL_S + 1) == "needs-review")
check("next_state human cause DOES expire past the review TTL", Q.next_state(1, "consent-borderline", Q.REVIEW_TTL_S + 1) == "expired")

check("backoff is exponential", Q.retry_backoff_s(0) == 30 and Q.retry_backoff_s(1) == 60 and Q.retry_backoff_s(2) == 120)
check("backoff is capped", Q.retry_backoff_s(20) == Q.BACKOFF_CAP_S)

# ============================ G6 — anti-scheduler drain order ============================
jobs = [{"id": "b", "seq": 2, "created": 1000.0, "state": "held"},   # arrived 2nd, but EARLIER clock
        {"id": "a", "seq": 1, "created": 5000.0, "state": "held"},   # arrived 1st, but LATER clock
        {"id": "r", "seq": 3, "created": 1.0, "state": "running"}]   # not held → excluded
order = Q.drain_order(jobs)
check("drain_order is strict arrival FIFO by seq", [j["id"] for j in order] == ["a", "b"])
check("drain_order is NOT ordered by created (clock-skew defense)", order[0]["created"] == 5000.0)
check("drain_order excludes non-held records", all(j["state"] == "held" for j in order))
halted = False
try:
    Q.drain_order([{"id": "x", "seq": 1, "state": "held", "priority": 9}])
except SystemExit:
    halted = True
check("a priority key HALTS the drainer (anti-scheduler)", halted)

# ============================ enqueue chokepoint ============================
refused_none = refused_priv = False
try:
    Q.enqueue(None, "x", spool=SPOOL)
except ValueError:
    refused_none = True
try:
    Q.enqueue("p1", "x", private=True, spool=SPOOL)
except ValueError:
    refused_priv = True
check("enqueue refuses job_id=None (no anonymous request in the durable spool)", refused_none)
check("enqueue refuses private=True (Condition 2 physical separation)", refused_priv)

# a real PNG to snapshot into the spool
png_src = os.path.join(_TMP, "seed.png")
open(png_src, "wb").write(b"PNGBYTES")
r1 = Q.enqueue("shot_a", "Create from image", png_src, spool=SPOOL)
r2 = Q.enqueue("shot_b", "Create from image", png_src, spool=SPOOL)
check("enqueue writes a held record", _held_files() == ["shot_a.held.json", "shot_b.held.json"])
check("enqueue allocates a monotonic seq", r1["seq"] == 1 and r2["seq"] == 2)
check("enqueue starts at attempts 0 / state held", r1["attempts"] == 0 and r1["state"] == "held")
check("enqueue snapshots the PNG into the spool (no URL re-fetch at drain)",
      r1["snapshot"] == os.path.join(SPOOL, "shot_a.png") and os.path.isfile(r1["snapshot"]))
check("the record carries NO priority/rank/weight field", not any(k in r1 for k in Q._FORBIDDEN_ORDER_KEYS))

# ============================ claim — atomic single-flight ============================
claimed = Q.claim(SPOOL, "shot_a")
check("claim returns the running record", claimed is not None and claimed["state"] == "running")
check("after claim the held marker is gone (renamed to running)", "shot_a.held.json" not in _held_files())
check("a claimed record is invisible to read_held (single-flight)", "shot_a" not in [j["id"] for j in Q.read_held(SPOOL)])
check("a second claim of the same id loses the race → None", Q.claim(SPOOL, "shot_a") is None)
check("running marker exists on disk", os.path.isfile(os.path.join(SPOOL, "shot_a.running.json")))

# ============================ writeback — outcomes ============================
# a failed GPU attempt: back to held, attempts++, backoff floor set
Q.claim(SPOOL, "shot_b")
state_b = Q.writeback(SPOOL, json.load(open(os.path.join(SPOOL, "shot_b.running.json"))), "gpu-busy")
recb = json.load(open(os.path.join(SPOOL, "shot_b.held.json")))
check("failed attempt returns to held", state_b == "held")
check("failed attempt bumped attempts", recb["attempts"] == 1)
check("failed attempt set a backoff floor (next_retry_after in the future)", recb["next_retry_after"] > 0)

# a 'done' run clears the record + its snapshot
state_a = Q.writeback(SPOOL, claimed, "done")
check("done clears the running record", state_a == "done" and not os.path.isfile(os.path.join(SPOOL, "shot_a.running.json")))
check("done removes the snapshot too", not os.path.isfile(os.path.join(SPOOL, "shot_a.png")))

# a human cause escalates straight to needs-review (the review.json sidecar lane)
Q.enqueue("shot_c", "Create from image", spool=SPOOL)
Q.claim(SPOOL, "shot_c")
state_c = Q.writeback(SPOOL, json.load(open(os.path.join(SPOOL, "shot_c.running.json"))), "b2-cant-verify")
check("a human cause escalates to needs-review", state_c == "needs-review")
check("needs-review is filed under the review suffix", os.path.isfile(os.path.join(SPOOL, "shot_c.review.json")))
check("needs_review_items surfaces it for the G1 sidecar",
      [i["id"] for i in Q.needs_review_items(SPOOL)] == ["shot_c"])

# ============================ crash recovery decides from the FILE ============================
Q.enqueue("shot_d", "Create from image", spool=SPOOL)
Q.claim(SPOOL, "shot_d")   # leaves shot_d.running.json — simulate a fire that died mid-run
recovered = Q.recover_crashed(SPOOL)
check("recover_crashed returns the orphan to held", ("shot_d", "held") in recovered)
check("recovered record is held again with attempts bumped",
      json.load(open(os.path.join(SPOOL, "shot_d.held.json")))["attempts"] == 1)
check("no running marker survives recovery", not os.path.isfile(os.path.join(SPOOL, "shot_d.running.json")))

# ============================ expire is terminal + idempotent ============================
Q.expire(SPOOL, "shot_b")
check("expire removes the record across all states", not _held_files().__contains__("shot_b.held.json"))
Q.expire(SPOOL, "shot_b")   # idempotent — no raise on a second call
check("expire is idempotent", True)

# ============================ durable_dir matches the feed.rs reader path ============================
check("durable_dir is $XDG_DATA_HOME/agentos/lucid-queue",
      Q.durable_dir() == os.path.join(_TMP, "data", "agentos", "lucid-queue"))

# --- cleanup ---
import shutil  # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)

print(f"lucid_queue: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
