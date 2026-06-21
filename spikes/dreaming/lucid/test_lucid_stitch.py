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

# ============================== summary ==============================
print(f"\n{ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
import shutil  # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if fail else 0)
