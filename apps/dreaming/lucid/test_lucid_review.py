#!/usr/bin/env python3
"""Unit tests for lucid_review (ADR-0019 §6, G1) — the review.json sidecar producer. Verifies the
exact contract feed.rs::read_lucid_review consumes: the path, the schema, a RECOMPUTED pending_review,
the updated_at heartbeat, and an atomic write (no temp left behind). No GPU/daemon/model.
Run: python3 test_lucid_review.py

Sets XDG_DATA_HOME to a temp dir BEFORE import so nothing touches the real ~/.local/share."""
import json
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="lucid_review_test_")
os.environ["XDG_DATA_HOME"] = _TMP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_review as R  # noqa: E402

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


QDIR = os.path.join(_TMP, "agentos", "lucid-queue")
RPATH = os.path.join(QDIR, "review.json")

# --- the path MUST match feed.rs::lucid_data_path("review.json") or reader & writer diverge ---
check("review_path is $XDG_DATA_HOME/agentos/lucid-queue/review.json", R.review_path() == RPATH)

# --- write recomputes pending_review from the authoritative item set ---
items = [{"id": "shot_a1", "title": "Create from image", "since": 1000.0},
         {"id": "shot_b2", "title": "Create from image", "since": 1001.0}]
R.write(items, now=2000.0)
doc = json.load(open(RPATH))
check("schema is 1", doc["schema"] == 1)
check("pending_review == len(items)", doc["pending_review"] == 2)
check("updated_at carried through", doc["updated_at"] == 2000.0)
check("items round-trip intact", doc["items"] == items)
check("feed.rs-shaped: pending_review int + updated_at float",
      isinstance(doc["pending_review"], int) and isinstance(doc["updated_at"], float))

# --- atomic write: no .review.*.tmp left in the dir ---
check("no temp file left behind", [e for e in os.listdir(QDIR) if e.endswith(".tmp")] == [])

# --- pending_review is RECOMPUTED, never incremented: fewer items lowers the count ---
R.write([items[0]], now=2001.0)
doc = json.load(open(RPATH))
check("pending_review recomputed DOWN to 1 (not a cached +=)", doc["pending_review"] == 1)

# --- heartbeat preserves the item set and bumps updated_at (the liveness tick) ---
R.heartbeat(now=2002.0)
doc = json.load(open(RPATH))
check("heartbeat keeps the items", len(doc["items"]) == 1)
check("heartbeat bumps updated_at", doc["updated_at"] == 2002.0)

# --- clear → a LIVE empty file (count 0) so the bloom retracts promptly, not by going stale ---
R.clear(now=2003.0)
doc = json.load(open(RPATH))
check("clear zeroes pending_review and items", doc["pending_review"] == 0 and doc["items"] == [])
check("clear still writes a live updated_at", doc["updated_at"] == 2003.0)

# --- heartbeat on a missing file is safe (treated as empty, never raises) ---
os.remove(RPATH)
R.heartbeat(now=2004.0)
doc = json.load(open(RPATH))
check("heartbeat on an absent file writes an empty live doc", doc["pending_review"] == 0)

# --- cleanup ---
import shutil  # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)

print(f"lucid_review: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
