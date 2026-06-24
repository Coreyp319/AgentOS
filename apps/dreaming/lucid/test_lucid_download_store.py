#!/usr/bin/env python3
"""Unit tests for the download-scratch storage helpers in lucid_store — the privacy/cleanup floor
under GET /api/download. No ffmpeg, no daemon, no GPU; pure filesystem behavior.

Covers:
  * make_download_workdir(private=True)  -> a fresh 0700 dir under the tmpfs lucid-dl root.
  * make_download_workdir(private=False) -> a fresh 0700 dir under the OS temp dir.
  * make_download_workdir(private=True) REFUSES a planted symlink at the lucid-dl root (the
    sealed-dir defense raises rather than following the link).
  * clear_download_scratch() reaps BOTH sinks (the tmpfs root AND orphaned OS-temp lucid-dl-* dirs),
    is own-guarded (never rmtree's a symlink/foreign dir sharing the prefix), and reports honestly.

Run: python3 test_lucid_download_store.py
"""
import os
import shutil
import sys
import tempfile

# Isolate BOTH scratch sinks into a throwaway sandbox before importing the module under test, and
# force tempfile to re-read TMPDIR (gettempdir() caches its answer on first use).
_TMP = tempfile.mkdtemp(prefix="lucid_dlstore_")
os.environ["XDG_RUNTIME_DIR"] = os.path.join(_TMP, "run")
os.environ["TMPDIR"] = os.path.join(_TMP, "ostmp")
os.makedirs(os.environ["XDG_RUNTIME_DIR"], exist_ok=True)
os.makedirs(os.environ["TMPDIR"], exist_ok=True)
tempfile.tempdir = None   # drop the cached temp dir so gettempdir() honors our TMPDIR

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_store as ST   # noqa: E402

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


def mode(path):
    return os.stat(path).st_mode & 0o777


# ---- make_download_workdir(private=True): sealed tmpfs subdir ----
priv_root = os.path.join(os.environ["XDG_RUNTIME_DIR"], "agentos", "lucid-dl")
wd_priv = ST.make_download_workdir(True)
check("private workdir lives under the tmpfs lucid-dl root", os.path.dirname(wd_priv) == priv_root)
check("private workdir is a real dir", os.path.isdir(wd_priv))
check("private workdir is 0700", mode(wd_priv) == 0o700)
check("the lucid-dl root itself is 0700 (sealed)", mode(priv_root) == 0o700)

# ---- make_download_workdir(private=False): OS-temp dir ----
wd_pub = ST.make_download_workdir(False)
check("non-private workdir lives in the OS temp dir", wd_pub.startswith(tempfile.gettempdir()))
check("non-private workdir basename carries the lucid-dl- prefix",
      os.path.basename(wd_pub).startswith("lucid-dl-"))
check("non-private workdir is a real dir", os.path.isdir(wd_pub))

# ---- clear_download_scratch reaps BOTH sinks ----
# Drop a sentinel file in each so we can prove the *contents* (a stitched MP4) are gone, not just the dir.
open(os.path.join(wd_priv, "dream.mp4"), "w").write("private bytes")
open(os.path.join(wd_pub, "dream.mp4"), "w").write("public bytes")
cleared = ST.clear_download_scratch()
check("clear_download_scratch reports it cleared something", cleared is True)
check("the tmpfs lucid-dl root is gone (private scratch reaped)", not os.path.exists(priv_root))
check("the orphaned OS-temp workdir is gone (non-private scratch reaped)", not os.path.exists(wd_pub))

# ---- idempotent / honest: nothing left to clear -> False ----
check("clear_download_scratch with nothing to do -> False", ST.clear_download_scratch() is False)

# ---- private path REFUSES a planted symlink at the root (no follow-the-link write) ----
os.makedirs(os.path.join(os.environ["XDG_RUNTIME_DIR"], "agentos"), exist_ok=True)
decoy = os.path.join(_TMP, "decoy-target")
os.makedirs(decoy, exist_ok=True)
os.symlink(decoy, priv_root)   # plant a symlink where the sealed root should be
raised = False
try:
    ST.make_download_workdir(True)
except (PermissionError, OSError):
    raised = True
check("make_download_workdir(private) refuses a symlinked root", raised)
check("nothing was written through the planted symlink", os.listdir(decoy) == [])
os.unlink(priv_root)

# ---- own-guard: clear_download_scratch must NOT rmtree a foreign/symlinked lucid-dl-* in OS temp ----
real_target = os.path.join(_TMP, "not-ours")
os.makedirs(real_target, exist_ok=True)
open(os.path.join(real_target, "keep.txt"), "w").write("must survive")
link = os.path.join(tempfile.gettempdir(), "lucid-dl-symlink-decoy")
os.symlink(real_target, link)   # a symlink whose name matches the glob
ST.clear_download_scratch()
check("clear_download_scratch does not follow/rmtree a symlinked lucid-dl-* (own-guard)",
      os.path.exists(os.path.join(real_target, "keep.txt")))
try:
    os.unlink(link)
except OSError:
    pass

# ============================== summary ==============================
print(f"\n{ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if fail else 0)
