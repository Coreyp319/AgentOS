#!/usr/bin/env python3
"""Lucid stitch — join a dream's per-beat clips into one downloadable MP4.

A dream is a chain of nodes (lucid_store); each non-opening node carries a `clip` (the MP4 a
beat rendered). This module concatenates the clips along the story spine (root -> tip) into a
single MP4 the surface offers for download. Two paths, chosen by probing the inputs:

  re-encode (DEFAULT) -- every clip is letterboxed to a common WxH + a constant fps and re-encoded
                         with the concat filter into ONE clean continuous stream (libx264, CPU only
                         — the GPU lease is never touched, so a download can't preempt a dream).
                         This is the download deliverable: a single CFR/yuv420p/+faststart MP4 plays
                         in every browser, phone, and desktop player.
  stream copy (opt-in)-- with prefer_copy=True AND uniform inputs, concat-demux with `-c copy`:
                         lossless and ~instant, BUT it stitches each clip's own edit-lists/timestamps
                         verbatim, which some players reject at a segment boundary or the tail. So it
                         is NOT the default for a downloaded file — correctness of playback wins over
                         speed/losslessness for an export.

SECURITY / PRIVACY
  * clip paths come ONLY from the server-held chain (never user input) and reach ffmpeg through
    a concat list FILE, never a shell — no argv-as-shell, no injection surface. Even so, every
    path is single-quote-escaped per the ffmpeg concat spec before it is written.
  * this module only writes where the caller tells it to. lucid_web routes a PRIVATE dream's
    output into a tmpfs workdir (privacy posture: no private byte on shared disk) and removes it
    after streaming. The stitcher itself is privacy-unaware on purpose — one owner of paths.
"""
import json
import os
import subprocess
from shutil import which

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# Defensive ceiling on the input list handed to ffmpeg. A dream is user-paced and naturally short
# (a handful to a few dozen beats); this guards a pathological/corrupt chain. Kept modest because
# the re-encode fallback builds a single filter_complex with one input per clip — a huge list is a
# memory hazard in ffmpeg itself, not just a long list (review: resource-safety + security).
MAX_CLIPS = 200

# lucid's default portrait format — the re-encode fallback's last resort if probing yields nothing.
_DEFAULT_W, _DEFAULT_H, _DEFAULT_FPS = 720, 1280, 16


class StitchError(RuntimeError):
    """Stitching could not produce a valid MP4 (ffmpeg missing, no clips, or an encode failure)."""


def have_ffmpeg():
    """Both binaries present on PATH — checked before any stitch so the web layer can 503 cleanly."""
    return bool(which(FFMPEG) and which(FFPROBE))


def clip_spine(chain):
    """The ordered clip paths along the story spine: root -> the newest beat.

    The chain's nodes are a flat list with `parent` pointers (a tree; a linear dream is the
    one-lane case). The tip is `nodes[-1]` — the most-recently appended beat, matching the
    surface's `tipId`. We walk parent pointers from the tip back to the root and reverse, so a
    BRANCHED dream yields the coherent path to its newest take rather than the interleaved
    all-takes order "Play all" would replay. The opening node is clip-less and drops out; a
    clip missing on disk (a partially-purged dream) is skipped so the rest still downloads.

    Returns [abs_clip_path, ...] in play order (possibly empty).
    """
    nodes = (chain or {}).get("nodes") or []
    if not nodes:
        return []
    by_id = {n.get("id"): n for n in nodes}
    spine, seen = [], set()
    cur = nodes[-1]
    while cur is not None and cur.get("id") not in seen:   # cycle / dangling-parent guard
        seen.add(cur.get("id"))
        spine.append(cur)
        parent = cur.get("parent")
        cur = by_id.get(parent) if parent is not None else None
    spine.reverse()   # root -> tip
    out = []
    for n in spine:
        # ADR-0033: prefer the HERO re-render when a keeper was finalized; fall back to the draft clip
        # (and skip if the preferred file was purged). The hero is the same shot at higher fidelity, so
        # play order / continuity is unchanged.
        c = n.get("hero_clip") or n.get("clip")
        if c and os.path.isfile(c):
            out.append(os.path.abspath(c))
        elif n.get("hero_clip") and n.get("clip") and os.path.isfile(n["clip"]):
            out.append(os.path.abspath(n["clip"]))   # hero file missing on disk -> draft still plays
    return out


def _ratio(r):
    """ffprobe rationals like '16/1' or '24000/1001' -> float fps; None on 0/N or garbage."""
    try:
        a, _, b = str(r or "").partition("/")
        b = b or "1"
        return round(float(a) / float(b), 3) if float(b) and float(a) else None
    except (ValueError, ZeroDivisionError):
        return None


def _probe(path):
    """ffprobe the first video stream -> a signature dict, or None on any failure (treated as
    'unknown', which forces the safe re-encode path)."""
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,width,height,pix_fmt,avg_frame_rate",
             "-of", "json", path],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30).stdout
        st = (json.loads(out or b"{}").get("streams") or [{}])[0]
        return {"codec": st.get("codec_name"), "width": st.get("width"),
                "height": st.get("height"), "pix_fmt": st.get("pix_fmt"),
                "fps": _ratio(st.get("avg_frame_rate"))}
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None


def _uniform(probes):
    """True iff every clip shares the params a clean `-c copy` concat needs: a known codec, identical
    dimensions, pixel format, AND frame rate. fps is included deliberately — a stream copy across
    clips of differing fps can emit non-monotonic DTS (a stuttery/garbled file that still probes as
    'valid'), so any fps mismatch is routed to the re-encode path (which normalizes fps). In practice
    a single dream from one engine is uniform; only an engine switch (wan<->10eros) trips this."""
    if not probes or any(p is None for p in probes):
        return False
    sig = {(p["codec"], p["width"], p["height"], p["pix_fmt"], p["fps"]) for p in probes}
    return len(sig) == 1 and all(p["codec"] and p["width"] and p["height"] for p in probes)


def _write_concat_list(paths, list_path):
    """Write the concat-demuxer list file. Each path is single-quoted with the ffmpeg-documented
    escape (' -> '\\'') so even a path with an apostrophe can't break out of its entry. Paths are
    ours (validated session dirs), but path-safety is never delegated to that assumption."""
    with open(list_path, "w") as f:
        for p in paths:
            esc = os.path.abspath(p).replace("'", "'\\''")
            f.write(f"file '{esc}'\n")


def _is_valid_mp4(path):
    """A produced file counts only if it exists, is non-empty, and probes as a real video."""
    if not (os.path.isfile(path) and os.path.getsize(path) > 0):
        return False
    p = _probe(path)
    return bool(p and p.get("width") and p.get("height"))


def _try_stream_copy(paths, out_path, timeout):
    """Lossless concat-demux with `-c copy`. Returns True iff it produced a valid MP4. Raises
    nothing for a non-zero ffmpeg exit — it returns False so the caller falls back to re-encode."""
    list_path = out_path + ".concat.txt"
    _write_concat_list(paths, list_path)
    try:
        subprocess.run(
            # +genpts: regenerate presentation timestamps so a copy never emits non-monotonic DTS.
            # -map_metadata -1: drop ALL source container metadata — a clip can carry the generation
            # prompt in a `comment`/`title` tag (ComfyUI VHS save_metadata); the download must not
            # smuggle that into a file the user shares (privacy review). Cheap belt-and-suspenders.
            [FFMPEG, "-y", "-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i", list_path,
             "-map_metadata", "-1", "-c", "copy", "-movflags", "+faststart", out_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise   # a timed-out copy ABORTS the stitch — never silently fall through to a 2nd full pass
    except subprocess.SubprocessError:
        return False   # a non-zero exit (mismatched params slipped through) -> try the re-encode
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass
    return _is_valid_mp4(out_path)


def _target(probes):
    """Common WxH + fps for the re-encode: the first probeable clip's dimensions (the dream's
    opening establishes the format) and the max fps seen (no clip is slowed down). Dimensions are
    forced even (libx264 / yuv420p require it). Falls back to lucid's default portrait."""
    dims = next((p for p in probes if p and p.get("width") and p.get("height")), None)
    w = int(dims["width"]) if dims else _DEFAULT_W
    h = int(dims["height"]) if dims else _DEFAULT_H
    fpses = [p["fps"] for p in probes if p and p.get("fps")]
    fps = max(fpses) if fpses else _DEFAULT_FPS
    return (w - w % 2, h - h % 2, fps)


def _reencode(paths, out_path, timeout, target):
    """Normalize every clip to `target` (WxH + fps) and concat-filter them into one re-encoded MP4.
    Letterbox (decrease + pad), not crop, so nothing in any beat is lost. libx264 → CPU only."""
    w, h, fps = target
    args = [FFMPEG, "-y"]
    for p in paths:
        args += ["-i", p]
    parts = [
        f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps}[v{i}]"
        for i in range(len(paths))
    ]
    parts.append("".join(f"[v{i}]" for i in range(len(paths)))
                 + f"concat=n={len(paths)}:v=1:a=0[outv]")
    args += ["-filter_complex", ";".join(parts), "-map", "[outv]",
             "-map_metadata", "-1",   # drop source metadata (prompt-bearing tags) — privacy review
             # max-compat deliverable: High-profile yuv420p, forced CFR, faststart (moov up front).
             # The per-input fps filter already makes it CFR; -fps_mode cfr + -r pin it so no player
             # sees a variable-rate tail (the "won't play at the end of a clip" failure).
             "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
             "-preset", "veryfast", "-crf", "20", "-fps_mode", "cfr", "-r", str(fps),
             "-movflags", "+faststart", out_path]
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    return _is_valid_mp4(out_path)


def stitch(clip_paths, out_path, timeout=600, prefer_copy=False):
    """Concatenate `clip_paths` into `out_path` (an MP4). By DEFAULT re-encodes into one clean
    constant-frame-rate stream — the maximally-compatible download deliverable. With prefer_copy=True
    AND uniform inputs, first attempts a lossless stream-copy (instant, but stitches each clip's own
    timestamps, which some players reject at a boundary/tail — opt-in only). Re-encode is the fallback
    whenever copy is not attempted or fails. Never touches the GPU. Raises StitchError on failure.

    Returns `out_path` on success.
    """
    if not have_ffmpeg():
        raise StitchError("ffmpeg/ffprobe not found")
    paths = [os.path.abspath(p) for p in clip_paths if p and os.path.isfile(p)][:MAX_CLIPS]
    if not paths:
        raise StitchError("no clips to stitch")
    probes = [_probe(p) for p in paths]

    if prefer_copy and _uniform(probes):
        try:
            if _try_stream_copy(paths, out_path, timeout):
                return out_path
        except subprocess.TimeoutExpired:
            raise StitchError("stream-copy timed out")
    try:
        if _reencode(paths, out_path, timeout, _target(probes)):
            return out_path
    except subprocess.TimeoutExpired:
        raise StitchError("re-encode timed out")
    except subprocess.SubprocessError as e:
        raise StitchError(f"re-encode failed: {e}")
    raise StitchError("stitch produced no valid MP4")
