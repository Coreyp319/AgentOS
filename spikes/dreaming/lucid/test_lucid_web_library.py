#!/usr/bin/env python3
"""End-to-end HTTP smoke test for the ADR-0028 multi-session web surface: the saved-dream library
(start-many / list / reopen / rename) and the encrypted private stash (init/unlock/save/lock/open/
delete) — driven through the REAL lucid_web Handler. No GPU/daemon/model: starts use the synthetic
abstract opening (no Ollama/ComfyUI), and readiness is stubbed so nothing hits the network.

Run: python3 test_lucid_web_library.py"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer

_TMP = tempfile.mkdtemp(prefix="lucid_web_lib_test_")
os.environ["XDG_RUNTIME_DIR"] = os.path.join(_TMP, "run")
os.environ["LUCID_DREAMS"] = os.path.join(_TMP, "dreams")
os.environ["COMFY_ROOT"] = os.path.join(_TMP, "comfy")
os.environ["LUCID_STASH"] = os.path.join(_TMP, "stash")
os.environ.pop("LUCID_WEB_SESSION", None)
for sub in ("run", "comfy/input", "comfy/output", "dreams"):
    os.makedirs(os.path.join(_TMP, *sub.split("/")), exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_web as W   # noqa: E402

# stub readiness so /api/state and the image path never touch the network (no coordinator/ollama here)
W.readiness = lambda: {"coordinator": True, "comfyui": False, "comfyui_on_demand": True,
                       "ollama": True, "can_dream": True, "why": []}

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


srv = ThreadingHTTPServer(("127.0.0.1", 0), W.Handler)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"


def _req(method, path, body=None, token=W.CSRF):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Lucid-Token"] = token
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, None


def post(path, body=None, token=W.CSRF):
    return _req("POST", path, body if body is not None else {}, token)


def get(path):
    return _req("GET", path)


def get_raw(path):
    """GET that returns (status, bytes) — for the binary thumbnail endpoint."""
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, None


# ---- CSRF guard ----
code, _ = post("/api/start", {}, token=None)
check("POST without CSRF token -> 403", code == 403)

# ---- start TWO named dreams; they coexist (no clobber) ----
_, a = post("/api/start", {"name": "Alpha"})
sess_a = a.get("session")
_, b = post("/api/start", {"name": "Beta"})
sess_b = b.get("session")
check("two starts return two distinct sessions", a.get("ok") and b.get("ok") and sess_a != sess_b)
check("session ids are slugged from the name", sess_a.startswith("Alpha-") and sess_b.startswith("Beta-"))

_, st = get("/api/state")
check("current dream is the most-recently-started (Beta)", st["session"] == sess_b and st["name"] == "Beta")
check("state carries stash status (not yet initialized)", st["stash"]["exists"] is False)

_, lib = get("/api/library")
names = {d["session"]: d["name"] for d in lib["dreams"]}
check("library lists BOTH saved dreams (no clobber)", names.get(sess_a) == "Alpha" and names.get(sess_b) == "Beta")

# thumbnail of a saved dream (synthetic opening wrote a real n0 frame)
code, data = get_raw(f"/api/library/thumb?session={sess_a}")
check("library thumbnail serves the tip frame (200, PNG)", code == 200 and data and data[:4] == b"\x89PNG")
code, _ = get_raw("/api/library/thumb?session=does-not-exist")
check("library thumbnail 404s an unknown dream", code == 404)

# ---- reopen Alpha; rename it ----
_, op = post("/api/open", {"session": sess_a})
_, st = get("/api/state")
check("reopen switches the current dream", op.get("ok") and st["session"] == sess_a)
_, rn = post("/api/rename", {"name": "Alpha renamed"})
_, lib = get("/api/library")
names = {d["session"]: d["name"] for d in lib["dreams"]}
check("rename updates the library label", rn.get("ok") and names.get(sess_a) == "Alpha renamed")
_, bad = post("/api/open", {"session": "no-such-dream"})
check("reopen of an unknown dream errors (no switch)", bad.get("error"))

# ================= the encrypted private stash =================
_, ini = post("/api/stash/init", {"passphrase": "open-sesame"})
check("stash init ok + auto-unlocked", ini.get("ok"))
_, stash = get("/api/stash")
check("stash now exists + unlocked + empty", stash["exists"] and stash["unlocked"] and stash["dreams"] == [])
_, dup = post("/api/stash/init", {"passphrase": "x"})
check("second init refused", dup.get("error"))

# a PRIVATE dream becomes the active dream, and we save IT (it is current + private)
_, p = post("/api/start", {"name": "Secret", "private": True})
sess_p = p.get("session")
_, st = get("/api/state")
check("private dream is active + flagged", st["session"] == sess_p and st["private"] is True)
_, sv = post("/api/stash/save", {})
sid = sv.get("id")
check("private dream saved to the stash", sv.get("ok") and sid)
_, stash = get("/api/stash")
check("stash lists the saved dream by name", len(stash["dreams"]) == 1 and stash["dreams"][0]["name"] == "Secret")

# lock -> reseals + burns the open working copy, drops the key, returns to a clean slate
_, lk = post("/api/stash/lock", {})
_, stash = get("/api/stash")
check("after lock: locked, no entries exposed", lk.get("ok") and stash["unlocked"] is False and "dreams" not in stash)
_, st = get("/api/state")
check("after lock the open private working copy is gone", st["session"] != sess_p)

# wrong passphrase stays locked; right one unlocks
_, w = post("/api/stash/unlock", {"passphrase": "WRONG"})
check("wrong passphrase rejected", w.get("ok") is False and w.get("error"))
_, u = post("/api/stash/unlock", {"passphrase": "open-sesame"})
check("right passphrase unlocks", u.get("ok") is True)

# save refuses a NON-private dream (real API path: reopen a library dream, then try to stash it)
post("/api/open", {"session": sess_a})
_, nope = post("/api/stash/save", {})
check("save refuses a non-private dream", nope.get("error"))

# reopen the stashed dream from ciphertext
_, stash = get("/api/stash")
sid = stash["dreams"][0]["id"]
_, so = post("/api/stash/open", {"id": sid})
_, st = get("/api/state")
check("stash open restores a live private dream", so.get("ok") and st["private"] is True
      and st["session"] == so["session"])

# delete from stash
_, dl = post("/api/stash/delete", {"id": sid})
_, stash = get("/api/stash")
check("delete removes the stash entry", dl.get("ok") and stash["dreams"] == [])
_, st = get("/api/state")
check("deleting the open dream returns to a clean slate", st["chain"] is None)

srv.shutdown()
shutil.rmtree(_TMP, ignore_errors=True)
print(f"lucid_web_library: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
