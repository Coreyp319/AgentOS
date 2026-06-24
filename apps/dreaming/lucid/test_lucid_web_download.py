#!/usr/bin/env python3
"""Integration test for GET /api/download — the whole-dream MP4 stitch route.

Drives the real Handler over a real socket against a temp dreams cache with real ffmpeg clips:
  * download by ?session= (a library dream) -> 200, video/mp4, attachment w/ a slugged name,
    Content-Length matched, and a valid ~2s MP4 in the body.
  * download with no ?session -> the CURRENT dream.
  * a PRIVATE dream downloads AND leaves no scratch dir behind (tmpfs workdir cleaned).
  * a clip-less dream -> 404; a bad/unknown session -> 400/404.

Needs ffmpeg + ffprobe. Run: python3 test_lucid_web_download.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

_TMP = tempfile.mkdtemp(prefix="lucid_dl_web_")
os.environ["XDG_RUNTIME_DIR"] = os.path.join(_TMP, "run")
os.environ["LUCID_DREAMS"] = os.path.join(_TMP, "dreams")
os.environ["COMFY_ROOT"] = os.path.join(_TMP, "comfy")
os.environ["LUCID_WEB_PORT"] = "8791"
os.environ["LUCID_WEB_HOST"] = "127.0.0.1"
for d in ("run", "dreams", os.path.join("comfy", "input"), os.path.join("comfy", "output")):
    os.makedirs(os.path.join(_TMP, d), exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_web as W   # noqa: E402
import lucid_store as ST   # noqa: E402
import lucid_stitch as STCH   # noqa: E402

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


def mkclip(path, w=320, h=240, rate=10, dur=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                    "-i", f"testsrc=duration={dur}:size={w}x{h}:rate={rate}",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", path],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return path


def probe_dur(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "json", path], check=True,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    return float(json.loads(out)["format"]["duration"])


if not STCH.have_ffmpeg():
    print("NOTE: ffmpeg/ffprobe not on PATH — skipping the download integration test")
    print("\n0 passed, 0 failed")
    sys.exit(0)

# ---- a 2-clip persistent (library) dream ----
sess = "testdream-a1b2c3"
ST.ensure_session(sess, False)
c1 = mkclip(os.path.join(_TMP, "c1.mp4"))
c2 = mkclip(os.path.join(_TMP, "c2.mp4"))
ST.save_chain(sess, False, {"session": sess, "private": False, "name": "Test Dream!! v2",
    "nodes": [{"id": 0, "parent": None, "clip": None, "out_frame": f"{sess}_n0.png"},
              {"id": 1, "parent": 0, "clip": c1, "out_frame": "n1.png"},
              {"id": 2, "parent": 1, "clip": c2, "out_frame": "n2.png"}]})

# ---- a clip-less dream (opening only) ----
empty = "emptydream-x9y8z7"
ST.save_chain(empty, False, {"session": empty, "private": False,
    "nodes": [{"id": 0, "parent": None, "clip": None, "out_frame": "o.png"}]})

# ---- a PRIVATE 1-clip dream (its tmpfs session dir makes is_private True) ----
psess = "privdream-q1w2e3"
ST.ensure_session(psess, True)
pc1 = mkclip(os.path.join(_TMP, "pc1.mp4"))
ST.save_chain(psess, True, {"session": psess, "private": True, "name": "Secret",
    "nodes": [{"id": 0, "parent": None, "clip": None, "out_frame": "po.png"},
              {"id": 1, "parent": 0, "clip": pc1, "out_frame": "pn1.png"}]})

# ---- bring up the server in a thread ----
from http.server import ThreadingHTTPServer   # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", W.PORT), W.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{W.PORT}"


def get(path):
    try:
        r = urllib.request.urlopen(urllib.request.Request(BASE + path), timeout=120)
        return r.status, {k: v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k: v for k, v in e.headers.items()}, e.read()


try:
    # 1) download a library dream by explicit ?session=
    st, h, body = get(f"/api/download?session={sess}")
    check("library download -> 200", st == 200)
    check("content-type video/mp4", h.get("Content-Type") == "video/mp4")
    cd = h.get("Content-Disposition", "")
    check("attachment + slugged filename", "attachment" in cd and 'filename="Test-Dream-v2.mp4"' in cd)
    check("content-length matches body", h.get("Content-Length") == str(len(body)) and len(body) > 0)
    check("no-store (a stitched temp is never cached)", h.get("Cache-Control") == "no-store")
    outp = os.path.join(_TMP, "dl.mp4")
    open(outp, "wb").write(body)
    check("downloaded body is a valid ~2s mp4",
          STCH._is_valid_mp4(outp) and abs(probe_dur(outp) - 2.0) < 0.5)

    # 2) current-session download (no ?session=)
    W.set_session(sess)
    st2, _, b2 = get("/api/download")
    check("current-session download -> 200 with bytes", st2 == 200 and len(b2) > 0)

    # 3) a PRIVATE dream downloads, and its tmpfs scratch dir is cleaned up afterward
    dl_base = os.path.join(os.environ["XDG_RUNTIME_DIR"], "agentos", "lucid-dl")
    st3, h3, b3 = get(f"/api/download?session={psess}")
    check("private download -> 200 with bytes", st3 == 200 and len(b3) > 0)
    # the server's finally-rmtree runs AFTER the client has its Content-Length bytes, so poll briefly
    # (this asserts cleanup, not timing): the scratch base must drain to empty.
    import time as _t
    for _ in range(50):
        leftover = os.listdir(dl_base) if os.path.isdir(dl_base) else []
        if not leftover:
            break
        _t.sleep(0.1)
    check("private stitch scratch dir cleaned (no tmpfs leftovers)", leftover == [])

    # 4) clip-less dream -> 404
    st4, _, _ = get(f"/api/download?session={empty}")
    check("clip-less dream -> 404", st4 == 404)

    # 5) bad session name -> 400 (path-safety)
    st5, _, _ = get("/api/download?session=../etc")
    check("bad session -> 400", st5 == 400)

    # 6) unknown session -> 404
    st6, _, _ = get("/api/download?session=nope-zzzzzz")
    check("unknown session -> 404", st6 == 404)

    # 7) concurrent download -> 503 (the one-stitch-at-a-time guard). Hold the permit ourselves and
    #    assert a request sees a clean 503, not an oversubscribed second ffmpeg.
    got = W._DOWNLOAD_SEM.acquire(blocking=False)
    check("test could grab the download permit", got)
    st7, _, b7 = get(f"/api/download?session={sess}")
    check("concurrent download -> 503", st7 == 503 and b"already being prepared" in b7)
    W._DOWNLOAD_SEM.release()
    # and the permit is healthy afterward — a normal download still works.
    st7b, _, b7b = get(f"/api/download?session={sess}")
    check("download works again after the permit is released", st7b == 200 and len(b7b) > 0)

    # 8) REGRESSION (semaphore leak): if make_download_workdir raises (full tmpfs / sealed-dir refusal),
    #    the route must NOT leak the permit. Force it to raise, assert a clean 500, then prove a later
    #    download still succeeds (a leaked permit would 503 forever).
    _orig_mkwd = ST.make_download_workdir
    try:
        def _boom_mkwd(private):
            raise OSError("simulated: no space left on device")
        ST.make_download_workdir = _boom_mkwd
        st8, _, _ = get(f"/api/download?session={sess}")
        check("workdir creation failure -> clean 500 (not a dropped connection)", st8 == 500)
    finally:
        ST.make_download_workdir = _orig_mkwd
    st8b, _, b8b = get(f"/api/download?session={sess}")
    check("download still works after a workdir failure (permit was released, no leak)",
          st8b == 200 and len(b8b) > 0)

    # 9) StitchError -> 500, and the permit is released (next download works).
    _orig_stitch = STCH.stitch
    try:
        def _boom_stitch(*a, **k):
            raise STCH.StitchError("simulated encode failure")
        STCH.stitch = _boom_stitch
        st9, _, b9 = get(f"/api/download?session={sess}")
        check("StitchError -> 500 with the reason", st9 == 500 and b"could not stitch" in b9)
    finally:
        STCH.stitch = _orig_stitch
    st9b, _, _ = get(f"/api/download?session={sess}")
    check("download works after a StitchError (permit released)", st9b == 200)

    # 10) ffmpeg absent -> 503 (the route checks have_ffmpeg before spawning anything).
    _orig_have = STCH.have_ffmpeg
    try:
        STCH.have_ffmpeg = lambda: False
        st10, _, b10 = get(f"/api/download?session={sess}")
        check("ffmpeg absent -> 503", st10 == 503 and b"ffmpeg" in b10)
    finally:
        STCH.have_ffmpeg = _orig_have

    # 11) a dream whose tip carries hero_clip (ADR-0033) downloads the HERO render, not the draft.
    hsess = "herodream-h1h2h3"
    ST.ensure_session(hsess, False)
    draft = mkclip(os.path.join(_TMP, "hero_draft.mp4"), 320, 240, 10, 1)   # 1s draft
    heroc = mkclip(os.path.join(_TMP, "hero_keep.mp4"), 320, 240, 10, 2)    # 2s hero (distinguishable)
    ST.save_chain(hsess, False, {"session": hsess, "private": False, "name": "Hero",
        "nodes": [{"id": 0, "parent": None, "clip": None, "out_frame": "ho.png"},
                  {"id": 1, "parent": 0, "clip": draft, "hero_clip": heroc, "out_frame": "hn1.png"}]})
    st11, _, b11 = get(f"/api/download?session={hsess}")
    outh = os.path.join(_TMP, "hero_dl.mp4"); open(outh, "wb").write(b11)
    check("hero_clip dream downloads the hero render (~2s, not the 1s draft)",
          st11 == 200 and STCH._is_valid_mp4(outh) and abs(probe_dur(outh) - 2.0) < 0.5)

    # 12) a PRIVATE dream that fails mid-stitch must STILL clean its tmpfs scratch (no private leftover).
    _orig_stitch2 = STCH.stitch
    try:
        def _boom_stitch2(*a, **k):
            raise STCH.StitchError("simulated private encode failure")
        STCH.stitch = _boom_stitch2
        st12, _, _ = get(f"/api/download?session={psess}")
        check("private StitchError -> 500", st12 == 500)
    finally:
        STCH.stitch = _orig_stitch2
    import time as _t2
    for _ in range(50):
        leftover12 = os.listdir(dl_base) if os.path.isdir(dl_base) else []
        if not leftover12:
            break
        _t2.sleep(0.1)
    check("private StitchError still sweeps the tmpfs scratch (no leftover)", leftover12 == [])

    # 13) _download_filename slugging edge cases (header-safety + friendliness), tested directly.
    check("filename: punctuation-only name falls back to the session id, not a shared 'dream'",
          W._download_filename({"name": "!!!@@@###"}, "sessabc123") == "sessabc123.mp4")
    fn_uni = W._download_filename({"name": "Réve éveillé 夢"}, "s")
    check("filename: unicode name slugs to ASCII-only [A-Za-z0-9._-]",
          fn_uni.endswith(".mp4") and re.fullmatch(r"[A-Za-z0-9._-]+", fn_uni) is not None)
    fn_long = W._download_filename({"name": "x" * 200}, "s")
    check("filename: long name capped to 60 + .mp4", len(fn_long) <= 64 and fn_long.endswith(".mp4"))
    check("filename: None name falls back to the session id",
          W._download_filename({"name": None}, "abc123") == "abc123.mp4")
    fn_trail = W._download_filename({"name": "a" * 59 + "----tail"}, "s")
    check("filename: 60-char cut never leaves a trailing separator before .mp4",
          not fn_trail[:-4].endswith(("-", "_", ".")))

    # 14) a BRANCHED dream exports EVERY take, not just the played spine. Tree: 0(open) -> 1(ba,1s);
    #     node 1 has two children — 2(bb,2s) continues one take, 3(bc,1s) branches another. The tip is
    #     node 3, so the spine is 1->3 = ba+bc = ~2s. The EXPORT must include the abandoned take's bb too,
    #     so the downloaded file is ~4s (ba+bb+bc). This is the "export all videos" guarantee.
    bsess = "branchdream-b1r2a3"
    ST.ensure_session(bsess, False)
    ba = mkclip(os.path.join(_TMP, "ba.mp4"), 320, 240, 10, 1)   # 1s
    bb = mkclip(os.path.join(_TMP, "bb.mp4"), 320, 240, 10, 2)   # 2s  (the take the spine DROPS)
    bc = mkclip(os.path.join(_TMP, "bc.mp4"), 320, 240, 10, 1)   # 1s  (the branch -> tip)
    ST.save_chain(bsess, False, {"session": bsess, "private": False, "name": "Branched",
        "nodes": [{"id": 0, "parent": None, "clip": None, "out_frame": "bo.png"},
                  {"id": 1, "parent": 0, "clip": ba, "out_frame": "bn1.png"},
                  {"id": 2, "parent": 1, "clip": bb, "out_frame": "bn2.png"},
                  {"id": 3, "parent": 1, "clip": bc, "out_frame": "bn3.png"}]})
    # sanity: the spine really is the shorter 2-clip path, so a passing ~4s export can't be the spine.
    check("sanity: spine is the 2-clip path (ba+bc)", STCH.clip_spine(ST.load_chain(bsess, False)) == [ba, bc])
    st14, _, b14 = get(f"/api/download?session={bsess}")
    outb = os.path.join(_TMP, "branch_dl.mp4"); open(outb, "wb").write(b14)
    check("branched dream exports ALL takes (~4s = ba+bb+bc, not the ~2s spine)",
          st14 == 200 and STCH._is_valid_mp4(outb) and abs(probe_dur(outb) - 4.0) < 0.6)
finally:
    srv.shutdown()

print(f"\n{ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
import shutil   # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if fail else 0)
