#!/usr/bin/env python3
"""Unit tests for lucid_stitch — the dream-clip concatenation (download-as-one-MP4).

Exercises the real ffmpeg paths with tiny generated clips:
  * clip_spine ordering: linear, branched (ancestry-to-tip, NOT interleaved), opening dropped,
    missing-on-disk skipped, empty chain.
  * _uniform signature logic.
  * stitch: uniform inputs (lossless stream-copy), heterogeneous inputs (re-encode fallback),
    single clip, a path containing an apostrophe (concat-list escaping), and the no-clips error.

No GPU / daemon / model. Needs ffmpeg + ffprobe on PATH (the stitch tests skip cleanly without
them). Run: python3 test_lucid_stitch.py
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_stitch as STCH  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="lucid_stitch_test_")
ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


def mkclip(path, w=320, h=240, rate=10, dur=1):
    """Generate a tiny real H.264 MP4 with ffmpeg's testsrc."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc=duration={dur}:size={w}x{h}:rate={rate}",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return path


def dur(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    return float(json.loads(out)["format"]["duration"])


def node(i, parent, clip):
    return {"id": i, "parent": parent, "label": f"b{i}", "prompt": None,
            "clip": clip, "out_frame": f"n{i}.png"}


# ============================== clip_spine (no ffmpeg needed) ==============================
# linear chain: opening (no clip) -> c1 -> c2 -> c3. spine = the three clips, in order, opening dropped.
c1, c2, c3 = (os.path.join(_TMP, f"c{i}.mp4") for i in (1, 2, 3))
for p in (c1, c2, c3):
    open(p, "w").write("x")   # spine only needs the files to EXIST (it os.path.isfile-gates)
linear = {"nodes": [node(0, None, None), node(1, 0, c1), node(2, 1, c2), node(3, 2, c3)]}
check("spine: linear yields clips in root->tip order, opening dropped",
      STCH.clip_spine(linear) == [c1, c2, c3])

# branched chain: 0 -> 1 -> 2, then a NEW take 3,4 grown from node 1 (parent=1). tip = nodes[-1] = 4.
# ancestry-to-tip is 1 -> 3 -> 4 (the coherent newest take), NOT the interleaved 1,2,3,4.
c4 = os.path.join(_TMP, "c4.mp4"); open(c4, "w").write("x")
branched = {"nodes": [node(0, None, None), node(1, 0, c1), node(2, 1, c2),
                      node(3, 1, c3), node(4, 3, c4)]}
check("spine: branched follows ancestry-to-tip (coherent take), not interleaved",
      STCH.clip_spine(branched) == [c1, c3, c4])

# a clip missing on disk is skipped, the rest survive (a partially-purged dream still downloads).
gone = os.path.join(_TMP, "gone.mp4")   # never created
withgap = {"nodes": [node(0, None, None), node(1, 0, c1), node(2, 1, gone), node(3, 2, c3)]}
check("spine: a missing-on-disk clip is skipped", STCH.clip_spine(withgap) == [c1, c3])

check("spine: empty chain -> []", STCH.clip_spine({"nodes": []}) == [])
check("spine: None chain -> []", STCH.clip_spine(None) == [])

# a dangling parent / id cycle must not loop forever
cyc = {"nodes": [node(0, 9, None), node(1, 0, c1)]}   # node 0's parent (9) doesn't exist
check("spine: dangling parent terminates", STCH.clip_spine(cyc) == [c1])

# ============================== _uniform signature ==============================
P = lambda c, w, h, pf, f: {"codec": c, "width": w, "height": h, "pix_fmt": pf, "fps": f}
check("_uniform: identical sigs -> True",
      STCH._uniform([P("h264", 720, 1280, "yuv420p", 16), P("h264", 720, 1280, "yuv420p", 16)]))
check("_uniform: differing resolution -> False",
      not STCH._uniform([P("h264", 720, 1280, "yuv420p", 16), P("h264", 480, 854, "yuv420p", 16)]))
check("_uniform: a None probe -> False",
      not STCH._uniform([P("h264", 720, 1280, "yuv420p", 16), None]))
check("_uniform: differing fps -> False (routed to fps-normalizing re-encode)",
      not STCH._uniform([P("h264", 720, 1280, "yuv420p", 16), P("h264", 720, 1280, "yuv420p", 24)]))
check("_uniform: empty -> False", not STCH._uniform([]))

# ============================== stitch (real ffmpeg) ==============================
if not STCH.have_ffmpeg():
    print("NOTE: ffmpeg/ffprobe not on PATH — skipping the real-stitch tests")
else:
    # --- DEFAULT: re-encode into one clean CFR stream (the download deliverable) ---
    u1 = mkclip(os.path.join(_TMP, "u1.mp4"), 320, 240, 10, 1)
    u2 = mkclip(os.path.join(_TMP, "u2.mp4"), 320, 240, 10, 1)
    out_u = os.path.join(_TMP, "out_uniform.mp4")
    STCH.stitch([u1, u2], out_u)   # default = re-encode (max-compat), even for uniform inputs
    check("stitch default -> a valid mp4", STCH._is_valid_mp4(out_u))
    check("stitch default -> duration ~= sum of inputs", abs(dur(out_u) - 2.0) < 0.4)
    pu = STCH._probe(out_u)
    check("stitch default -> geometry preserved for uniform inputs",
          pu and pu["width"] == 320 and pu["height"] == 240)
    check("stitch default -> CFR at the input rate (single continuous stream)",
          pu and pu["fps"] == 10)

    # --- opt-in lossless stream-copy path (prefer_copy=True) on uniform inputs ---
    out_copy = os.path.join(_TMP, "out_copy.mp4")
    STCH.stitch([u1, u2], out_copy, prefer_copy=True)
    check("stitch prefer_copy uniform -> a valid mp4", STCH._is_valid_mp4(out_copy))
    check("stitch prefer_copy uniform -> duration ~= sum", abs(dur(out_copy) - 2.0) < 0.4)

    # --- heterogeneous inputs (different res AND fps): forces the re-encode fallback ---
    h1 = mkclip(os.path.join(_TMP, "h1.mp4"), 320, 240, 10, 1)
    h2 = mkclip(os.path.join(_TMP, "h2.mp4"), 480, 320, 15, 1)
    out_h = os.path.join(_TMP, "out_hetero.mp4")
    STCH.stitch([h1, h2], out_h)
    check("stitch heterogeneous -> a valid mp4 (re-encode path)", STCH._is_valid_mp4(out_h))
    check("stitch heterogeneous -> duration ~= sum of inputs", abs(dur(out_h) - 2.0) < 0.6)
    ph = STCH._probe(out_h)
    # target = first clip's dims (320x240), every later clip letterboxed into it
    check("stitch heterogeneous -> normalized to the first clip's frame",
          ph and ph["width"] == 320 and ph["height"] == 240)

    # --- single clip stitches to a valid (essentially copied) mp4 ---
    out_one = os.path.join(_TMP, "out_one.mp4")
    STCH.stitch([u1], out_one)
    check("stitch single clip -> valid mp4", STCH._is_valid_mp4(out_one))

    # --- concat-list escaping: a directory whose name contains an apostrophe ---
    qdir = os.path.join(_TMP, "corey's dreams")
    q1 = mkclip(os.path.join(qdir, "q1.mp4"), 320, 240, 10, 1)
    q2 = mkclip(os.path.join(qdir, "q2.mp4"), 320, 240, 10, 1)
    out_q = os.path.join(_TMP, "out_quote.mp4")
    STCH.stitch([q1, q2], out_q)
    check("stitch path-with-apostrophe -> valid mp4 (concat-list escaped)", STCH._is_valid_mp4(out_q))

    # --- metadata is stripped: a clip carrying the prompt in a comment/title tag must NOT leak it
    #     into the downloaded MP4 (privacy: VHS save_metadata embeds the prompt). Both paths. ---
    def tag_clip(src, dst):
        subprocess.run(["ffmpeg", "-y", "-i", src, "-map_metadata", "-1",
                        "-metadata", "comment=SECRET_PROMPT_about_a_private_dream",
                        "-metadata", "title=secret-title", "-c", "copy", dst],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return dst

    def fmt_tags(path):
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags",
                              "-of", "json", path], check=True,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
        return json.dumps(json.loads(out).get("format", {}).get("tags", {}))

    tagged = tag_clip(u1, os.path.join(_TMP, "tagged.mp4"))
    check("sanity: tagged input really carries the secret", "SECRET_PROMPT" in fmt_tags(tagged))
    # default (re-encode) path
    out_mr = os.path.join(_TMP, "out_meta_reenc.mp4")
    STCH.stitch([tagged, u2], out_mr)
    check("re-encode strips source metadata (no prompt leak)", "SECRET_PROMPT" not in fmt_tags(out_mr))
    # opt-in stream-copy path also strips it
    out_mc = os.path.join(_TMP, "out_meta_copy.mp4")
    STCH.stitch([tagged, u2], out_mc, prefer_copy=True)
    check("stream-copy strips source metadata (no prompt leak)", "SECRET_PROMPT" not in fmt_tags(out_mc))

    # --- no existing clips -> StitchError ---
    try:
        STCH.stitch([os.path.join(_TMP, "does-not-exist.mp4")], os.path.join(_TMP, "nope.mp4"))
        raised = False
    except STCH.StitchError:
        raised = True
    check("stitch no-clips -> StitchError", raised)

# ============================== clip_spine: hero_clip preference (ADR-0033) ==============================
# A keeper finalized at higher fidelity carries hero_clip; the spine should prefer it over the draft clip.
hero, draft = os.path.join(_TMP, "hero.mp4"), os.path.join(_TMP, "draft.mp4")
for p in (hero, draft):
    open(p, "w").write("x")
hn = {"id": 1, "parent": 0, "clip": draft, "hero_clip": hero, "out_frame": "h.png"}
hero_chain = {"nodes": [node(0, None, None), hn]}
check("spine: hero_clip is preferred over the draft clip", STCH.clip_spine(hero_chain) == [hero])

# hero set but the hero FILE was purged -> fall back to the draft clip (still on disk) so the dream plays.
hn_missing = {"id": 1, "parent": 0, "clip": draft, "hero_clip": os.path.join(_TMP, "no-hero.mp4")}
check("spine: hero set but hero file missing -> falls back to the draft clip",
      STCH.clip_spine({"nodes": [node(0, None, None), hn_missing]}) == [draft])

# hero set, hero present, draft missing -> hero (the common keeper case where the draft was cleaned).
hn_heroonly = {"id": 1, "parent": 0, "clip": os.path.join(_TMP, "no-draft.mp4"), "hero_clip": hero}
check("spine: hero present, draft purged -> uses the hero",
      STCH.clip_spine({"nodes": [node(0, None, None), hn_heroonly]}) == [hero])

# a node whose parent is its OWN id must terminate (the `seen` guard), not loop forever.
selfcyc = {"nodes": [node(0, None, None), {"id": 1, "parent": 1, "clip": c1}]}
check("spine: self-cycle (parent == own id) terminates", STCH.clip_spine(selfcyc) == [c1])

# ============================== _ratio (fps rational parsing) ==============================
_ratio_cases = [("24000/1001", 23.976), ("30000/1001", 29.97), ("16/1", 16.0), ("16", 16.0),
                ("0/1", None), ("0/0", None), ("16/0", None), ("", None), (None, None),
                ("abc/def", None), ("/1001", None)]
for raw, want in _ratio_cases:
    check(f"_ratio({raw!r}) == {want}", STCH._ratio(raw) == want)

# ============================== _uniform extra signatures ==============================
check("_uniform: codec present but width None -> False",
      not STCH._uniform([P("h264", None, 1280, "yuv420p", 16)]))
check("_uniform: a single fully-valid probe -> True",
      STCH._uniform([P("h264", 720, 1280, "yuv420p", 16)]))
check("_uniform: all-None -> False", not STCH._uniform([None, None]))
check("_uniform: identical sig but empty codec -> False (unknown codec forces re-encode)",
      not STCH._uniform([P("", 720, 1280, "yuv420p", 16), P("", 720, 1280, "yuv420p", 16)]))

# ============================== _target ==============================
check("_target: all-None probes -> default portrait", STCH._target([None, None]) == (720, 1280, 16))
check("_target: odd dimensions forced even (libx264/yuv420p require it)",
      STCH._target([P("h264", 721, 1281, "yuv420p", 16)]) == (720, 1280, 16))
check("_target: fps is the MAX across clips, not the first",
      STCH._target([P("h264", 720, 1280, "yuv420p", 16), P("h264", 720, 1280, "yuv420p", 24)])[2] == 24)
check("_target: valid dims but fps None -> dims kept, fps falls to default",
      STCH._target([P("h264", 640, 480, "yuv420p", None)]) == (640, 480, 16))

# ============================== CPU-only invariant + thread cap (argv guard, no ffmpeg run) ==============================
# Capture the re-encode argv WITHOUT spawning ffmpeg: the download must stay CPU-only (libx264, never
# NVENC/CUDA/hwaccel — those would contend for the VRAM the dream/wallpaper need) and bounded in threads.
_captured = {}
def _fake_run(args, **kw):
    _captured["args"] = args
    class _R: pass
    return _R()
_real_run = subprocess.run
try:
    subprocess.run = _fake_run
    STCH._reencode(["/x/a.mp4", "/x/b.mp4"], "/x/out.mp4", 600, (320, 240, 10))
finally:
    subprocess.run = _real_run
_argv = " ".join(_captured.get("args", []))
check("re-encode is CPU-only: libx264 in argv", "libx264" in _argv)
check("re-encode is CPU-only: no nvenc/cuda/hwaccel in argv",
      not any(t in _argv for t in ("nvenc", "cuda", "hwaccel")))
check("re-encode caps threads (leaves cores for inference)",
      "-threads" in _captured.get("args", []) and STCH._ENCODE_THREADS >= 1)
check("re-encode strips source metadata (-map_metadata -1)", "-map_metadata" in _argv and "-1" in _captured["args"])

# ============================== ffmpeg-missing -> StitchError (monkeypatched) ==============================
_real_have = STCH.have_ffmpeg
try:
    STCH.have_ffmpeg = lambda: False
    try:
        STCH.stitch([c1], os.path.join(_TMP, "nope.mp4"))
        check("stitch with no ffmpeg -> StitchError", False)
    except STCH.StitchError as e:
        check("stitch with no ffmpeg -> StitchError", "ffmpeg" in str(e).lower())
finally:
    STCH.have_ffmpeg = _real_have

# ============================== timeout -> StitchError (monkeypatched subprocess) ==============================
if STCH.have_ffmpeg():
    real1 = mkclip(os.path.join(_TMP, "to1.mp4"), 320, 240, 10, 1)
    real2 = mkclip(os.path.join(_TMP, "to2.mp4"), 320, 240, 10, 1)

    def _timeout_run(args, **kw):
        raise subprocess.TimeoutExpired(cmd=args, timeout=kw.get("timeout"))

    # re-encode timeout: _probe must still work (so the stitch reaches the encode), only the encode times out.
    _orig = subprocess.run
    def _probe_ok_else_timeout(args, **kw):
        if args and args[0] == STCH.FFPROBE:
            return _orig(args, **kw)
        raise subprocess.TimeoutExpired(cmd=args, timeout=kw.get("timeout"))
    try:
        subprocess.run = _probe_ok_else_timeout
        try:
            STCH.stitch([real1, real2], os.path.join(_TMP, "to_out.mp4"))
            check("re-encode timeout -> StitchError", False)
        except STCH.StitchError as e:
            check("re-encode timeout -> StitchError", "timed out" in str(e))
    finally:
        subprocess.run = _orig

    # stream-copy timeout ABORTS the stitch (never silently falls through to a full 2nd pass).
    def _probe_ok_else_timeout_copy(args, **kw):
        if args and args[0] == STCH.FFPROBE:
            return _orig(args, **kw)
        raise subprocess.TimeoutExpired(cmd=args, timeout=kw.get("timeout"))
    try:
        subprocess.run = _probe_ok_else_timeout_copy
        try:
            STCH.stitch([real1, real2], os.path.join(_TMP, "to_copy.mp4"), prefer_copy=True)
            check("stream-copy timeout -> StitchError", False)
        except STCH.StitchError as e:
            check("stream-copy timeout -> StitchError", "timed out" in str(e))
    finally:
        subprocess.run = _orig

# ============================== real-ffmpeg edge cases ==============================
if STCH.have_ffmpeg():
    # --- a present-but-corrupt clip is SKIPPED; the good clip still downloads (not an all-or-nothing 500) ---
    good = mkclip(os.path.join(_TMP, "good.mp4"), 320, 240, 10, 1)
    corrupt = os.path.join(_TMP, "corrupt.mp4")
    open(corrupt, "w").write("not a video at all, just bytes")   # exists, isfile True, but undecodable
    out_mix = os.path.join(_TMP, "out_mix.mp4")
    STCH.stitch([good, corrupt], out_mix)
    check("stitch [good, corrupt] -> valid mp4 of just the good clip",
          STCH._is_valid_mp4(out_mix) and abs(dur(out_mix) - 1.0) < 0.4)

    # --- ALL clips corrupt -> StitchError (the floor: nothing decodable, the encode is the final judge) ---
    corrupt2 = os.path.join(_TMP, "corrupt2.mp4"); open(corrupt2, "w").write("garbage two")
    try:
        STCH.stitch([corrupt, corrupt2], os.path.join(_TMP, "out_allbad.mp4"))
        check("stitch [all corrupt] -> StitchError", False)
    except STCH.StitchError:
        check("stitch [all corrupt] -> StitchError", True)

    # --- prefer_copy=True with NON-uniform inputs (different res+fps) falls back to re-encode ---
    nu1 = mkclip(os.path.join(_TMP, "nu1.mp4"), 320, 240, 10, 1)
    nu2 = mkclip(os.path.join(_TMP, "nu2.mp4"), 480, 320, 15, 1)
    out_nu = os.path.join(_TMP, "out_nonuniform_copy.mp4")
    STCH.stitch([nu1, nu2], out_nu, prefer_copy=True)   # copy declined (not uniform) -> re-encode
    pnu = STCH._probe(out_nu)
    check("prefer_copy + non-uniform -> valid re-encoded mp4 normalized to the first clip",
          STCH._is_valid_mp4(out_nu) and pnu and pnu["width"] == 320 and pnu["height"] == 240)

    # --- the re-encode deliverable carries NO audio stream (concat a=0 — documents the deliberate drop) ---
    awav = os.path.join(_TMP, "with_audio.mp4")
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", awav],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def _has_audio(path):
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                              "stream=codec_type", "-of", "json", path], check=True,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
        return bool(json.loads(out).get("streams"))
    check("sanity: the audio input really has an audio stream", _has_audio(awav))
    out_audio = os.path.join(_TMP, "out_audio.mp4")
    STCH.stitch([awav, good], out_audio)
    check("re-encode download has NO audio stream (concat a=0)", not _has_audio(out_audio))

    # --- MAX_CLIPS truncation keeps the NEWEST beats (the tip), not the oldest ---
    t1 = mkclip(os.path.join(_TMP, "t1.mp4"), 320, 240, 10, 1)   # 1s
    t2 = mkclip(os.path.join(_TMP, "t2.mp4"), 320, 240, 10, 1)   # 1s
    t3 = mkclip(os.path.join(_TMP, "t3.mp4"), 320, 240, 10, 2)   # 2s (the newest)
    _orig_max = STCH.MAX_CLIPS
    try:
        STCH.MAX_CLIPS = 2
        out_cap = os.path.join(_TMP, "out_cap.mp4")
        STCH.stitch([t1, t2, t3], out_cap)   # keeps the last 2: t2(1s)+t3(2s) = ~3s, NOT t1+t2 = ~2s
        check("MAX_CLIPS keeps the NEWEST beats (tail), dropping the oldest",
              STCH._is_valid_mp4(out_cap) and abs(dur(out_cap) - 3.0) < 0.5)
    finally:
        STCH.MAX_CLIPS = _orig_max

    # --- _is_valid_mp4 negatives ---
    zero = os.path.join(_TMP, "zero.mp4"); open(zero, "w").close()
    check("_is_valid_mp4: 0-byte file -> False", not STCH._is_valid_mp4(zero))
    nonvid = os.path.join(_TMP, "nonvid.mp4"); open(nonvid, "w").write("plain text, not a video")
    check("_is_valid_mp4: non-video file -> False", not STCH._is_valid_mp4(nonvid))
    check("_is_valid_mp4: missing file -> False", not STCH._is_valid_mp4(os.path.join(_TMP, "ghost.mp4")))

# ============================== summary ==============================
print(f"\n{ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
import shutil  # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if fail else 0)
