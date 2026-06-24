#!/usr/bin/env python3
"""Unit tests for the Lucid job board (the :8765 right-click creation queue). No GPU/model/daemon.
Uses a throwaway XDG_RUNTIME_DIR so it never touches the real board. Run: python3 test_lucid_jobs.py"""
import os
import sys
import tempfile

# Point the board at a throwaway dir BEFORE importing (JOBS_DIR is resolved at import time).
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="jobs_test_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_jobs as J

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


# --- create / recent / update lifecycle ---
jid = J.create("Create from image", job_id="shot_abcd")
check("create returns the given id", jid == "shot_abcd")
r = J.recent()
check("created job appears, status queued", len(r) == 1 and r[0]["status"] == "queued")

J.update(jid, status="checking")
J.update(jid, status="generating")
check("status transitions persist", J.recent()[0]["status"] == "generating")

# bad status is ignored (never let a typo become a real state)
J.update(jid, status="bogus")
check("invalid status ignored", J.recent()[0]["status"] == "generating")

# --- clip_path: only a ready job with a real video file is servable ---
check("not-ready job -> no clip", J.clip_path(jid) is None)
clip = os.path.join(os.environ["XDG_RUNTIME_DIR"], "out.mp4")
open(clip, "wb").close()
J.update(jid, status="ready", clip=clip)
check("ready + real mp4 -> servable path", J.clip_path(jid) == clip)
J.update(jid, status="ready", clip=clip + ".gone")
check("ready + missing file -> None", J.clip_path(jid) is None)
J.update(jid, status="ready", clip="/etc/passwd")
check("ready + non-video path -> None", J.clip_path(jid) is None)

# --- id validation blocks path traversal ---
for bad in ["../escape", "a/b", "", "x" * 99, "semi;colon"]:
    try:
        J._path(bad)
        check(f"reject bad id {bad!r}", False)
    except ValueError:
        check(f"reject bad id {bad!r}", True)
check("clip_path on bad id -> None (no raise)", J.clip_path("../escape") is None)

# --- prune caps the board at MAX_JOBS ---
for i in range(J.MAX_JOBS + 8):
    J.create(f"job {i}")
check("board capped at MAX_JOBS", len(J.recent(limit=1000)) <= J.MAX_JOBS)

# --- newest-first ordering ---
times = [j.get("created", 0) for j in J.recent(limit=1000)]
check("recent() is newest-first", times == sorted(times, reverse=True))

print(f"lucid_jobs: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
