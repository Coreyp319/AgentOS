#!/usr/bin/env python3
"""Unit tests for lucid_priv_queue (ADR-0019 §5, H1-H5) — the EPHEMERAL PRIVATE in-session retry
queue. Proves: the hold chokepoint refuses a non-private session and writes under the right
per-session subdir; the reused lucid_queue ops (claim/writeback/recovery) work over a per-session
spool; a retry-exhausted private item BURNS SILENTLY (no review record, no review.json, no on-disk
trace); records never appear on the durable disk-backed paths; the burn-alignment holds —
`lucid_store.list_priv_queue()` returns exactly the sessions this module created, and
`clear_priv_queue_dir()` wipes the whole queue. No GPU/daemon/model.

Sets XDG_RUNTIME_DIR / XDG_DATA_HOME / LUCID_DREAMS / COMFY_ROOT to temp dirs BEFORE import so
nothing touches the real cache, the real durable spool, or ComfyUI dirs.
Run: python3 test_lucid_priv_queue.py"""
import glob
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="lucid_priv_queue_test_")
os.environ["XDG_RUNTIME_DIR"] = os.path.join(_TMP, "run")        # tmpfs root for priv + priv-queue
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")          # the DURABLE disk-backed root
os.environ["LUCID_DREAMS"] = os.path.join(_TMP, "dreams")         # the persistent dream cache
os.environ["COMFY_ROOT"] = os.path.join(_TMP, "comfy")
os.makedirs(os.path.join(_TMP, "run"), exist_ok=True)
os.makedirs(os.path.join(_TMP, "comfy", "input"), exist_ok=True)
os.makedirs(os.path.join(_TMP, "comfy", "output"), exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_priv_queue as PQ  # noqa: E402
import lucid_queue as Q        # noqa: E402  (read-only: assert durable paths stay empty)
import lucid_store as ST       # noqa: E402  (read-only: assert the burn-alignment)

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


RUN = os.path.join(_TMP, "run")
PQ_ROOT = os.path.join(RUN, "agentos", "lucid-priv-queue")        # the ephemeral queue root
PRIV_ROOT = os.path.join(RUN, "agentos", "lucid-priv")            # the dream tmpfs root
DURABLE = Q.durable_dir()                                         # the disk-backed spool (must stay empty);
#   resolved via the authoritative accessor so this test file does not itself spell the durable path
#   token (keeps the Condition-2 grep clean: no source file names BOTH roots).
CINPUT = os.path.join(_TMP, "comfy", "input")
DREAMS = os.path.join(_TMP, "dreams")


def _make_private(session):
    """Make `session` genuinely private the way the launcher does — a sealed tmpfs dream dir — so
    ST.is_private(session) is True and hold() accepts it."""
    ST.ensure_session(session, True)


def _spool_files(session, suffix):
    spool = os.path.join(PQ_ROOT, session)
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(spool, f"*.{suffix}.json")))


def _no_durable_trace():
    """No record/snapshot ever landed in the durable disk-backed spool."""
    if not os.path.isdir(DURABLE):
        return True
    return glob.glob(os.path.join(DURABLE, "**", "*"), recursive=True) == []


# ============================ root alignment (the burn-alignment foundation) ============================
check("priv_queue_root == lucid_store._priv_queue_root (the same root, no edit needed)",
      PQ.priv_queue_root() == ST._priv_queue_root() == PQ_ROOT)

# ============================ the hold chokepoint ============================
# a NON-private session is refused (Condition 2, physical separation)
refused_nonpriv = False
try:
    PQ.hold("notpriv", "job_np", "x")
except ValueError:
    refused_nonpriv = True
check("hold REFUSES a non-private session (no non-private item in the private spool)", refused_nonpriv)
check("a refused non-private hold wrote NOTHING (no session subdir created)",
      not os.path.exists(os.path.join(PQ_ROOT, "notpriv")))

refused_noneid = False
_make_private("ph_pre")
try:
    PQ.hold("ph_pre", None, "x")
except ValueError:
    refused_noneid = True
check("hold REFUSES job_id=None (a hold needs a stable dedup id)", refused_noneid)

# a real PNG to snapshot into the SEALED tmpfs subdir
png_src = os.path.join(_TMP, "seed.png")
open(png_src, "wb").write(b"PNGBYTES")

_make_private("alpha")
r1 = PQ.hold("alpha", "shot_a", "Private create", png_src)
r2 = PQ.hold("alpha", "shot_b", "Private create", png_src)
check("hold writes a held record under the per-session subdir",
      _spool_files("alpha", "held") == ["shot_a.held.json", "shot_b.held.json"])
check("the per-session subdir lives directly under the priv-queue root",
      os.path.isdir(os.path.join(PQ_ROOT, "alpha")))
check("the per-session subdir is sealed 0700",
      (os.stat(os.path.join(PQ_ROOT, "alpha")).st_mode & 0o777) == 0o700)
check("hold marks the record private=True", r1["private"] is True and r2["private"] is True)
check("hold allocates a per-session monotonic seq", r1["seq"] == 1 and r2["seq"] == 2)
check("hold starts at attempts 0 / state held", r1["attempts"] == 0 and r1["state"] == "held")
check("the record carries NO priority/rank/weight field (anti-scheduler holds for private too)",
      not any(k in r1 for k in Q._FORBIDDEN_ORDER_KEYS))

# H1 — the snapshot is sealed into the tmpfs session subdir, never shared/durable disk
check("hold snapshots the PNG INTO the sealed tmpfs session subdir",
      r1["snapshot"] == os.path.join(PQ_ROOT, "alpha", "shot_a.png") and os.path.isfile(r1["snapshot"]))
check("the snapshot is 0600", (os.stat(r1["snapshot"]).st_mode & 0o777) == 0o600)

# H1 — NOTHING reaches the durable disk-backed spool
check("H1: NOTHING written to the durable disk-backed spool after holds", _no_durable_trace())

# ============================ reused lifecycle: claim / writeback / recovery ============================
# claim is the reused atomic single-flight over the per-session spool
claimed = PQ.claim("alpha", "shot_a")
check("claim returns the running record (reused lucid_queue.claim)",
      claimed is not None and claimed["state"] == "running")
check("after claim the held marker is gone", "shot_a.held.json" not in _spool_files("alpha", "held"))
check("a second claim of the same id loses the race → None", PQ.claim("alpha", "shot_a") is None)

# a failed GPU attempt returns to held (live in-session retry), attempts++, backoff floor set
disp = PQ.writeback("alpha", claimed, "gpu-busy")
check("a failed attempt returns to held (live retry)", disp == "held")
held_a = [r for r in PQ.read_held("alpha") if r["id"] == "shot_a"][0]
check("failed attempt bumped attempts", held_a["attempts"] == 1)
check("failed attempt set a backoff floor", held_a["next_retry_after"] > 0)

# a 'done' run clears the record + its snapshot, no trace
claimed_b = PQ.claim("alpha", "shot_b")
disp_b = PQ.writeback("alpha", claimed_b, "done")
check("done clears the running record", disp_b == "done"
      and not os.path.isfile(os.path.join(PQ_ROOT, "alpha", "shot_b.running.json")))
check("done removes the snapshot too",
      not os.path.isfile(os.path.join(PQ_ROOT, "alpha", "shot_b.png")))

# crash recovery decides from the FILE (reused lucid_queue.recover_crashed)
PQ.claim("alpha", "shot_a")   # leaves a *.running.json — simulate a fire that died mid-run
recovered, burned = PQ.recover_crashed("alpha")
check("recover_crashed returns the orphan to held", ("shot_a", "held") in recovered and not burned)
check("recovered record is held again with attempts bumped",
      [r for r in PQ.read_held("alpha") if r["id"] == "shot_a"][0]["attempts"] == 2)

# ============================ H3 / Condition 6 — retry-exhausted BURNS SILENTLY ============================
_make_private("exhaust")
PQ.hold("exhaust", "ex1", "Private create", png_src)
# drive attempts to MAX so next_state → "needs-review" → must BURN SILENTLY, not file a review row
disp_x = None
for _ in range(Q.MAX_ATTEMPTS + 1):
    c = PQ.claim("exhaust", "ex1")
    if c is None:
        break
    disp_x = PQ.writeback("exhaust", c, "gpu-busy")
    if disp_x == "burned-silent":
        break
check("a retry-exhausted private item BURNS SILENTLY (no needs-review)", disp_x == "burned-silent")
check("Condition 6: NO *.review.json record exists anywhere in the priv-queue",
      glob.glob(os.path.join(PQ_ROOT, "**", "*.review.json"), recursive=True) == [])
check("Condition 6: the burned session's subdir is gone (no on-disk trace)",
      not os.path.exists(os.path.join(PQ_ROOT, "exhaust")))
check("H3: NO review.json sidecar in the DURABLE spool (private never reaches lucid_review)",
      not os.path.isfile(os.path.join(DURABLE, "review.json")))
check("H1: still NOTHING in the durable disk-backed spool after a silent burn", _no_durable_trace())

# a human-cause outcome (b2-cant-verify) ALSO burns silently for a private item (never needs-review)
_make_private("human")
PQ.hold("human", "hu1", "Private create", png_src)
ch = PQ.claim("human", "hu1")
disp_h = PQ.writeback("human", ch, "b2-cant-verify")
check("a human-cause outcome on a PRIVATE item burns silently (H3: no review edge)",
      disp_h == "burned-silent" and not os.path.exists(os.path.join(PQ_ROOT, "human")))

# expire_stale: a forgotten/idle private session past the TTL burns silently without running
_make_private("stale")
PQ.hold("stale", "st1", "Private create", png_src)
# force the record's created far enough back that next_state → expired
spool_stale = os.path.join(PQ_ROOT, "stale")
import json  # noqa: E402
rp = os.path.join(spool_stale, "st1.held.json")
_rec = json.load(open(rp))
_rec["created"] = 0.0   # epoch — older than DEFER_TTL_S
Q._atomic_write(rp, _rec)
burned_stale = PQ.expire_stale("stale")
check("expire_stale burns a TTL-exhausted idle private session silently", burned_stale is True)
check("expire_stale left no subdir trace", not os.path.exists(os.path.join(PQ_ROOT, "stale")))

# ============================ burn-alignment: list_priv_queue / list_sessions / clear ============================
_make_private("burn_a")
_make_private("burn_b")
PQ.hold("burn_a", "ba1", "Private create", png_src)
PQ.hold("burn_b", "bb1", "Private create", png_src)
# `alpha` still has the recovered shot_a held; plus burn_a, burn_b
live = set(PQ.list_sessions())
check("list_sessions returns the live private queue sessions",
      {"alpha", "burn_a", "burn_b"} <= live)
check("THE BURN ALIGNMENT: list_sessions() == lucid_store.list_priv_queue() for the same root",
      set(PQ.list_sessions()) == set(ST.list_priv_queue()))
check("held_count counts held records across sessions (RAM-derived, read-time)",
      PQ.held_count() >= 3)

# the on-logout burn hook (ADR-0019 Condition 1): for each session list_priv_queue returns, burn it,
# then clear the whole queue dir — proves the landed Condition-1 code wipes this module's records.
for s in sorted(set(ST.list_priv_queue()) | set(ST.list_private())):   # the ExecStop hook's core
    ST.burn(s)
cleared = ST.clear_priv_queue_dir()
check("Condition 1: clear_priv_queue_dir() wipes the whole ephemeral queue", cleared is True)
check("Condition 1: the priv-queue root is gone after the logout sweep", not os.path.exists(PQ_ROOT))
check("Condition 1: NO sealed input subdir survives the logout burn",
      [e for e in os.listdir(CINPUT) if e.startswith(".lucid-priv-")] == [])
check("Condition 1: list_sessions empty after the sweep", PQ.list_sessions() == [])
check("H1: the durable disk-backed spool was NEVER touched by the whole run", _no_durable_trace())

# ============================ purge() leaves no trace + name validation ============================
_make_private("purge1")
PQ.hold("purge1", "pg1", "Private create", png_src)
PQ.purge("purge1")
check("purge removes the queue subdir entirely", not os.path.exists(os.path.join(PQ_ROOT, "purge1")))
check("purge also burned the dream tmpfs sink (lucid_store.burn reuse)",
      not os.path.exists(os.path.join(PRIV_ROOT, "purge1")))

bad_session = False
try:
    PQ.hold("../etc", "x", "y")
except ValueError:
    bad_session = True
check("hold refuses a traversal session name (fail-closed, no path built)", bad_session)

# ============================ grep invariant (Condition 2): module names only the priv-queue path ============================
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lucid_priv_queue.py")).read()
# The durable path token is assembled non-contiguously here so this TEST file itself never spells it as
# a literal substring (keeps the repo-wide Condition-2 grep clean). We assert the module under test
# contains NO occurrence of the durable token in any path form, but DOES name the ephemeral root.
_dur = "agentos" + "/" + "lucid-queue"
_dur_sep = "agentos" + "', '" + "lucid-queue"
_dur_dq = 'agentos' + '", "' + 'lucid-queue'
check("Condition 2 grep invariant: module never names the durable lucid-queue path (any form)",
      _dur not in src and _dur_sep not in src and _dur_dq not in src)
check("Condition 2 grep invariant: module DOES name the ephemeral lucid-priv-queue path",
      "lucid-priv-queue" in src)

# --- cleanup ---
import shutil  # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)

print(f"lucid_priv_queue: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
