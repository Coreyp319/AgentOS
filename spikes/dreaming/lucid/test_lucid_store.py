#!/usr/bin/env python3
"""Unit tests for lucid_store (ADR-0016) — the persistent-vs-private routing, the no-trace
property, burn safety, and session-name validation. No GPU/daemon/model. Run: python3 test_lucid_store.py

Sets XDG_RUNTIME_DIR / LUCID_DREAMS / COMFY_ROOT to temp dirs BEFORE import so nothing touches
the real cache or ComfyUI dirs."""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="lucid_store_test_")
os.environ["XDG_RUNTIME_DIR"] = os.path.join(_TMP, "run")
os.environ["LUCID_DREAMS"] = os.path.join(_TMP, "dreams")
os.environ["COMFY_ROOT"] = os.path.join(_TMP, "comfy")
os.makedirs(os.path.join(_TMP, "run"), exist_ok=True)
os.makedirs(os.path.join(_TMP, "comfy", "input"), exist_ok=True)
os.makedirs(os.path.join(_TMP, "comfy", "output"), exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_store as ST  # noqa: E402

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


PRIV_ROOT = os.path.join(_TMP, "run", "agentos", "lucid-priv")
DREAMS = os.path.join(_TMP, "dreams")
CINPUT = os.path.join(_TMP, "comfy", "input")
COUTPUT = os.path.join(_TMP, "comfy", "output")

# --- session-name validation (the rmtree/path-traversal guard) ---
check("valid name ok", ST.valid_session("web") and ST.valid_session("Dream_1-2"))
check("traversal rejected", not ST.valid_session("../etc"))
check("slash rejected", not ST.valid_session("a/b"))
check("dotfile rejected", not ST.valid_session(".hidden"))
check("empty rejected", not ST.valid_session(""))
check("overlong rejected", not ST.valid_session("x" * 65))
check("non-str rejected", not ST.valid_session(None))
try:
    ST.burn("../../etc"); burned_bad = False
except ValueError:
    burned_bad = True
check("burn refuses invalid session (no rmtree on bad path)", burned_bad)

# --- routing: persistent vs private ---
check("persistent dir under dreams cache", ST.session_dir("s1", False) == os.path.join(DREAMS, "s1"))
check("private dir under XDG_RUNTIME_DIR tmpfs", ST.session_dir("s1", True) == os.path.join(PRIV_ROOT, "s1"))

# --- private start leaves NO trace in persistent cache or shared input root ---
ST.ensure_session("p1", True)
ref_name, abs_path = ST.frame_ref("p1", True, "p1_n0.png")
open(abs_path, "w").write("FRAMEBYTES")  # the sealed anchor
ST.save_chain("p1", True, {"session": "p1", "private": True, "nodes": []})
check("private chain lives in tmpfs", os.path.isfile(os.path.join(PRIV_ROOT, "p1", "chain.json")))
check("private frame ref is a sealed subpath", ref_name == ".lucid-priv-p1/p1_n0.png")
check("private frame on disk only in sealed 0700 subdir",
      os.path.isfile(os.path.join(CINPUT, ".lucid-priv-p1", "p1_n0.png")))
check("sealed frame subdir is 0700", (os.stat(os.path.join(CINPUT, ".lucid-priv-p1")).st_mode & 0o777) == 0o700)
check("NOTHING for p1 in the persistent dream cache", not os.path.exists(os.path.join(DREAMS, "p1")))
check("NOTHING for p1 in the shared input root (only the sealed subdir)",
      [e for e in os.listdir(CINPUT)] == [".lucid-priv-p1"])
check("private output_prefix targets a private subdir", ST.output_prefix("p1", True) == "lucid-priv-p1/clip")

# --- place_clip: private drains the WHOLE output subdir (clip + the prompt-bearing sidecar PNG) ---
shared_clip = os.path.join(COUTPUT, "lucid-priv-p1", "clip_00001.mp4")
sidecar = os.path.join(COUTPUT, "lucid-priv-p1", "clip_00001.png")  # embeds the prompt in metadata
os.makedirs(os.path.dirname(shared_clip), exist_ok=True)
open(shared_clip, "w").write("CLIPBYTES")
open(sidecar, "w").write("PROMPT-IN-METADATA")
moved = ST.place_clip("p1", True, shared_clip)
check("private clip moved into tmpfs", moved.startswith(PRIV_ROOT) and os.path.isfile(moved))
check("no private clip left in shared output", not os.path.isfile(shared_clip))
check("prompt-bearing sidecar PNG also drained (BLOCKER fix)", not os.path.isfile(sidecar))
check("shared output subdir removed entirely", not os.path.isdir(os.path.join(COUTPUT, "lucid-priv-p1")))

# --- persistent place_clip leaves the clip where ComfyUI wrote it ---
pclip = os.path.join(COUTPUT, "lucid", "keep.mp4")
os.makedirs(os.path.dirname(pclip), exist_ok=True)
open(pclip, "w").write("x")
check("persistent clip untouched", ST.place_clip("s1", False, pclip) == pclip and os.path.isfile(pclip))

# --- is_private detection ---
check("is_private true for sealed session", ST.is_private("p1") is True)
check("is_private false otherwise", ST.is_private("s1") is False)
check("list_private finds p1", "p1" in ST.list_private())

# --- BURN removes every private sink and leaves nothing ---
removed, failed = ST.burn("p1")
check("burn removed >=2 sinks", len(removed) >= 2)
check("burn reported no failures", failed == [])
check("burn: tmpfs session gone", not os.path.exists(os.path.join(PRIV_ROOT, "p1")))
check("burn: sealed input subdir gone", not os.path.exists(os.path.join(CINPUT, ".lucid-priv-p1")))
check("burn: output subdir gone", not os.path.exists(os.path.join(COUTPUT, "lucid-priv-p1")))
check("burn left the persistent cache untouched", os.path.isfile(pclip))

# --- symlink-safe seal: a planted symlink at the sealed input path is REFUSED (no write-redirect) ---
victim = os.path.join(_TMP, "victim")
os.makedirs(victim, exist_ok=True)
linkp = os.path.join(CINPUT, ".lucid-priv-sym1")
os.symlink(victim, linkp)
try:
    ST.frame_ref("sym1", True, "sym1_n0.png"); refused = False
except PermissionError:
    refused = True
check("frame_ref REFUSES a planted symlink seal", refused)
check("nothing written through the symlink to the victim", os.listdir(victim) == [])
os.unlink(linkp)

# --- frame name validation (latent traversal) ---
check("valid_name ok", ST.valid_name("web_n0.png"))
check("valid_name rejects traversal", not ST.valid_name("../x.png"))
check("valid_name rejects slash", not ST.valid_name("a/b.png"))
try:
    ST.frame_ref("p2", True, "../escape.png"); badname = False
except ValueError:
    badname = True
check("frame_ref rejects a bad frame name", badname)

# --- is_private is fail-closed: true from the sealed input subdir even after a tmpfs wipe ---
ST.ensure_session("orph1", True)
ST.frame_ref("orph1", True, "orph1_n0.png")          # creates the sealed input subdir
import shutil as _sh  # noqa: E402
_sh.rmtree(os.path.join(PRIV_ROOT, "orph1"))         # simulate logout wiping tmpfs
check("is_private still True from the sealed input subdir", ST.is_private("orph1") is True)

# --- reap_orphans burns a sink whose tmpfs session is gone ---
reaped = ST.reap_orphans()
check("reap_orphans found the orphan", "orph1" in reaped)
check("reap removed the sealed input subdir", not os.path.exists(os.path.join(CINPUT, ".lucid-priv-orph1")))
check("orphan no longer private after reap", ST.is_private("orph1") is False)

# --- cleanup ---
import shutil  # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)

print(f"lucid_store: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
