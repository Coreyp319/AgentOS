#!/usr/bin/env python3
"""Unit tests for lucid_stash (ADR-0028) — the encrypted, passphrase-locked private stash:
save/open round-trip into the live tmpfs sinks, the no-plaintext-on-disk property, lock gating,
rename/delete, reseal, and passphrase rotation. No GPU/daemon/model.

Temp XDG/dreams/comfy/stash roots are set BEFORE import so nothing touches the real cache.
Run: python3 test_lucid_stash.py"""
import json
import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="lucid_stash_test_")
os.environ["XDG_RUNTIME_DIR"] = os.path.join(_TMP, "run")
os.environ["LUCID_DREAMS"] = os.path.join(_TMP, "dreams")
os.environ["COMFY_ROOT"] = os.path.join(_TMP, "comfy")
os.environ["LUCID_STASH"] = os.path.join(_TMP, "stash")
os.makedirs(os.path.join(_TMP, "run"), exist_ok=True)
os.makedirs(os.path.join(_TMP, "comfy", "input"), exist_ok=True)
os.makedirs(os.path.join(_TMP, "comfy", "output"), exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_store as ST   # noqa: E402
import lucid_stash as SH   # noqa: E402
import lucid_crypto as C   # noqa: E402
import lucid_engine as E   # noqa: E402

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


SECRET_NAME = "My very private dream"
SECRET_PREMISE = "a hidden midnight garden"
CLIP_BYTES = b"\x00\x01fake-mp4-bytes\xff" * 50
FRAME0 = b"PNG-frame-zero-bytes"
FRAME1 = b"PNG-frame-one-bytes"


def make_live_private(session):
    """Build a 2-node live PRIVATE session straight through lucid_store, as the engine would leave it."""
    ST.ensure_session(session, True)
    tdir = ST.session_dir(session, True)
    clip_path = os.path.join(tdir, f"{session}_clip1.mp4")
    with open(clip_path, "wb") as f:
        f.write(CLIP_BYTES)
    r0, a0 = ST.frame_ref(session, True, f"{session}_n0.png")
    with open(a0, "wb") as f:
        f.write(FRAME0)
    r1, a1 = ST.frame_ref(session, True, f"{session}_n1.png")
    with open(a1, "wb") as f:
        f.write(FRAME1)
    chain = {"session": session, "private": True, "premise": SECRET_PREMISE, "nodes": [
        {"id": 0, "parent": None, "label": "opening", "prompt": None, "clip": None, "out_frame": r0},
        {"id": 1, "parent": 0, "label": "the gate opens", "prompt": "p", "clip": clip_path,
         "out_frame": r1, "rating": "sfw"},
    ]}
    ST.save_chain(session, True, chain)
    return clip_path


# --- init / unlock gating ---
check("stash absent before init", not SH.exists())
SH.init("hunter2-correct")
check("stash exists + unlocked after init", SH.exists() and SH.is_unlocked())
try:
    SH.init("again"); reinit = False
except FileExistsError:
    reinit = True
check("init refuses a second time", reinit)
check("fresh listing is empty", SH.listing() == [])

# --- save a live private session ---
make_live_private("priv1")
entry = SH.save_session("priv1", name=SECRET_NAME)
sid = entry["id"]
check("save returns a valid id", SH._valid_id(sid))
check("entry name + frame count", entry["name"] == SECRET_NAME and entry["frames"] == 2)
check("entry carries the premise", entry["premise"] == SECRET_PREMISE)
lst = SH.listing()
check("listing has the one dream", len(lst) == 1 and lst[0]["id"] == sid)
check("listing leaks no filesystem paths",
      all(not isinstance(v, str) or "/" not in v for e in lst for v in e.values() if v))

# --- the no-plaintext-on-disk property (names + premise live only in ciphertext) ---
on_disk = b""
for root, _d, files in os.walk(SH._root()):
    for fn in files:
        with open(os.path.join(root, fn), "rb") as f:
            on_disk += f.read()
check("dream NAME never appears in plaintext on disk", SECRET_NAME.encode() not in on_disk)
check("premise never appears in plaintext on disk", SECRET_PREMISE.encode() not in on_disk)
check("clip bytes never appear in plaintext on disk", CLIP_BYTES not in on_disk)
check("blob filename is the random id, not the name", os.path.isfile(SH._blob_path(sid)))

# --- lock gates everything ---
SH.lock()
check("locked", not SH.is_unlocked())
for fn in (lambda: SH.listing(), lambda: SH.save_session("priv1"), lambda: SH.open_into(sid)):
    try:
        fn(); guarded = False
    except RuntimeError:
        guarded = True
    check("operation refused while locked", guarded)

# --- wrong passphrase stays locked; right one unlocks ---
check("wrong passphrase -> False", SH.unlock("WRONG") is False and not SH.is_unlocked())
check("right passphrase -> True", SH.unlock("hunter2-correct") is True and SH.is_unlocked())

# --- burn the live working copy, prove open_into rebuilds it from ciphertext alone ---
ST.burn("priv1")
check("live working copy is gone before open", not ST.is_private("priv1"))
sess, chain = SH.open_into(sid)
check("restored into a private session", ST.is_private(sess) and sess == SH.restore_name(sid))
check("restored chain has both nodes + premise",
      len(chain["nodes"]) == 2 and chain["premise"] == SECRET_PREMISE)
n1 = chain["nodes"][1]
check("restored clip exists at the rewritten path",
      n1["clip"] and os.path.isfile(n1["clip"]))
with open(n1["clip"], "rb") as f:
    check("restored clip bytes round-trip", f.read() == CLIP_BYTES)
check("restored out_frame points into the restored sealed subdir (keeps original basename)",
      n1["out_frame"] == f".lucid-priv-{sess}/priv1_n1.png")
fr = os.path.join(E.INPUT_DIR, n1["out_frame"])
check("restored frame file exists in the sealed subdir", os.path.isfile(fr))
with open(fr, "rb") as f:
    check("restored frame bytes round-trip", f.read() == FRAME1)

# --- re-saving the OPENED session updates the same entry (no duplicate) ---
e2 = SH.save_session(sess)
check("re-save reuses the same id (no dup)", e2["id"] == sid and len(SH.listing()) == 1)

# --- rename ---
check("rename works", SH.rename(sid, "Renamed dream") and SH.listing()[0]["name"] == "Renamed dream")

# --- reseal_opened: re-encrypt + burn the working copy ---
resealed = SH.reseal_opened(burn=True)
check("reseal reports the id", sid in resealed)
check("working copy burned after reseal", not ST.is_private(sess))
check("stash entry still present after reseal", len(SH.listing()) == 1)

# --- change passphrase: old fails, new works, blobs re-encrypted (still openable) ---
check("change_passphrase True", SH.change_passphrase("hunter2-correct", "new-pass-9") is True)
SH.lock()
check("old passphrase rejected after rotation", SH.unlock("hunter2-correct") is False)
check("new passphrase accepted", SH.unlock("new-pass-9") is True)
sess2, chain2 = SH.open_into(sid)
check("dream still opens under the rotated key", len(chain2["nodes"]) == 2)
SH.reseal_opened(burn=True)

# --- delete ---
check("delete removes the entry + blob",
      SH.delete(sid) and SH.listing() == [] and not os.path.exists(SH._blob_path(sid)))

# --- a corrupt/foreign blob fails closed ---
SH._write_atomic(SH._blob_path("aaaaaaaaaaaa"), b"not a real blob")
try:
    SH.open_into("aaaaaaaaaaaa"); opened_junk = True
except C.BadData:
    opened_junk = False
check("a corrupt blob fails closed (BadData)", not opened_junk)

# --- crash-atomic passphrase rotation (security-review must-fix: never split across two keys) ---
make_live_private("priv2")
sid2 = SH.save_session("priv2", name="Rotation test")["id"]

# Test A: a crash AFTER the atomic meta commit but BEFORE promote → the next unlock finishes it,
# the NEW key wins, the OLD key is dead, and no .rekey sidecar survives.
_real_promote = SH._promote_rekey
SH._promote_rekey = lambda: None                       # simulate dying right after the commit
check("rotate (promote suppressed) returns True", SH.change_passphrase("new-pass-9", "rot-2") is True)
check("committed-unpromoted: a .rekey sidecar is staged", os.path.exists(SH._blob_path(sid2) + SH._REKEY_EXT))
SH._promote_rekey = _real_promote
SH.lock()
check("interrupted rotation: OLD pass is dead (commit happened)", SH.unlock("new-pass-9") is False)
check("interrupted rotation: NEW pass unlocks", SH.unlock("rot-2") is True)
check("resolve promoted the sidecar (none left)", not os.path.exists(SH._blob_path(sid2) + SH._REKEY_EXT))
cA = SH.open_into(sid2)[1]
check("dream opens under the resolved (new) key", len(cA["nodes"]) == 2)
SH.reseal_opened(burn=True)

# Test B: a crash DURING staging, BEFORE the commit → the orphan sidecar is discarded and the stash
# stays fully on the OLD (current) key, intact.
SH._write_atomic(SH._blob_path(sid2) + SH._REKEY_EXT, b"orphan staged under an uncommitted key")
SH.lock()
check("pre-commit orphan: current pass still unlocks", SH.unlock("rot-2") is True)
check("pre-commit orphan sidecar discarded", not os.path.exists(SH._blob_path(sid2) + SH._REKEY_EXT))
check("dream still listed after orphan cleanup", any(e["id"] == sid2 for e in SH.listing()))
check("dream still opens after orphan cleanup", len(SH.open_into(sid2)[1]["nodes"]) == 2)
SH.reseal_opened(burn=True)

# A clean rotation leaves NO sidecars behind, and the dream survives it.
check("clean rotation True", SH.change_passphrase("rot-2", "rot-3") is True)
check("clean rotation leaves no .rekey sidecars",
      not any(fn.endswith(SH._REKEY_EXT) for fn in os.listdir(SH._root())))
check("dream opens after the clean rotation", len(SH.open_into(sid2)[1]["nodes"]) == 2)
SH.reseal_opened(burn=True)

# --- a present-but-corrupt index fails closed (must NOT silently read as empty + orphan blobs) ---
SH._write_atomic(SH._index_path(), b"corrupt-not-a-real-blob")
try:
    SH.listing(); idx_failed_closed = False
except C.BadData:
    idx_failed_closed = True
check("a corrupt index fails closed (BadData), not silently empty", idx_failed_closed)

shutil.rmtree(_TMP, ignore_errors=True)
print(f"lucid_stash: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
