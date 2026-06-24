#!/usr/bin/env python3
"""Chain Wan 2.2 NSFW I2V segments into a longer clip (~30-60 s).

Route A from the local-video-gen skill: each segment inits from the *previous*
segment's last frame, so a model trained on ~5 s clips can cover a minute by
running N dependent rounds — then ffmpeg stitches them.

This is a SEQUENTIAL pipeline, not a parallel queue: segment N+1 cannot start
until segment N's last frame exists. Expect drift across a long chain (colour /
detail / anatomy creep); fewer, larger-frame segments drift less.

    # 30 s from a clean first frame (best anatomy for NSFW):
    ./chain_video.py --seed-image still.png --prompt "..." --duration 30

    # explicit segment count, T2V-seeded first segment (anatomy lottery):
    ./chain_video.py --seed-t2v --prompt "..." --segments 6

Per the skill's VRAM rules: free Ollama first (`sudo systemctl restart
ollama.service`) and ideally `systemctl --user restart comfyui.service` for a
clean ~20 GB before a heavy chain. Video-gen XOR live inference on the GPU.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comfy_client as cc

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.join(HERE, "workflows")
DEFAULT_I2V_WF = os.path.join(WF_DIR, "Wan2.2-Remix-NSFW-i2v-v3.0.json")
# official template (we have wan2.2 t2v high/low fp8 + lightx2v loras) — known-good seed
TEMPLATES = os.path.expanduser(
    "~/ComfyUI/.venv/lib/python3.12/site-packages/"
    "comfyui_workflow_templates_media_video/templates")
DEFAULT_T2V_WF = os.path.join(TEMPLATES, "video_wan2_2_14B_t2v.json")
INPUT_DIR = os.path.join(cc.COMFY_ROOT, "input")
FPS = 16


def log(msg):
    print(f"[chain] {msg}", flush=True)


def resolve_prompt_nodes(api):
    """Find the (positive, negative) CLIPTextEncode ids. cc.pos_neg_text_nodes follows
    the sampler links, but this workflow routes conditioning THROUGH WanImageToVideo and
    that resolver mis-maps the negative slot (returns pos==neg). Instead: pick the node
    whose positive AND negative inputs link directly to a CLIPTextEncode. Falls back to
    the library resolver for CFGGuider-style graphs."""
    for nid, node in api.items():
        ins = node.get("inputs", {})
        p, n = ins.get("positive"), ins.get("negative")
        if isinstance(p, list) and isinstance(n, list):
            if (api.get(p[0], {}).get("class_type") == "CLIPTextEncode" and
                    api.get(n[0], {}).get("class_type") == "CLIPTextEncode"):
                return p[0], n[0]
    return cc.pos_neg_text_nodes(api)


def load_graph(wf_path):
    """Return an API-format graph. Accepts either a UI workflow (converted via
    ui_to_api) or an already-API graph (dict of nodes each with class_type — e.g.
    workflows/enhNSFW-nolight-i2v.api.json, the non-distilled GGUF I2V set)."""
    wf = json.load(open(wf_path))
    if isinstance(wf, dict) and "nodes" not in wf and wf and all(
            isinstance(v, dict) and "class_type" in v for v in wf.values()):
        return dict(wf)
    return cc.ui_to_api(wf)


def interrupt():
    """A client timeout does NOT cancel the server job (skill rule #4). Best-effort
    cancel so a failed segment can't leave a runaway job behind the next submit."""
    try:
        req = urllib.request.Request(cc.BASE + "/interrupt", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def ffmpeg(args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + args,
                   check=True)


def extract_last_frame(video_path, out_png):
    """Grab the final frame as the next segment's init. -sseof seeks from EOF;
    -update 1 keeps overwriting so we end on the last decoded frame."""
    ffmpeg(["-sseof", "-1", "-i", video_path, "-update", "1", "-q:v", "2", out_png])
    if not os.path.exists(out_png):  # ultra-short clip: fall back to a frame index
        ffmpeg(["-i", video_path, "-vf", "select=eq(n\\,0)", "-vframes", "1",
                "-q:v", "2", out_png])


def normalize(seg_path, out_path, drop_first):
    """Re-encode to a canonical h264/yuv420p so concat -c copy is safe, and drop the
    duplicated seam frame on every segment after the first (its frame 0 == its init
    == the prior segment's last frame, else the join stutters)."""
    vf = ("select=gte(n\\,1),setpts=PTS-STARTPTS" if drop_first
          else "setpts=PTS-STARTPTS")
    ffmpeg(["-i", seg_path, "-vf", vf, "-r", str(FPS),
            "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-an", out_path])


def concat(parts, out_path):
    listfile = out_path + ".concat.txt"
    with open(listfile, "w") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    ffmpeg(["-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy", out_path])
    os.remove(listfile)


def interpolate(in_path, out_path, target_fps):
    """Optional motion-interpolation to stretch perceived smoothness/length cheaply.
    ffmpeg minterpolate (RIFE in-ComfyUI would be higher quality; out of scope here)."""
    ffmpeg(["-i", in_path, "-vf",
            f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir",
            "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", out_path])


def run_i2v_segment(wf_path, init_filename, prompt, negative, length, width, height,
                    out_prefix, timeout):
    api = load_graph(wf_path)
    pos, neg = resolve_prompt_nodes(api)
    if prompt is not None and pos:
        api[pos]["inputs"]["text"] = prompt
    if negative is not None and neg:
        api[neg]["inputs"]["text"] = negative
    cc.set_input(api, "LoadImage", "image", init_filename)
    cc.set_input(api, "WanImageToVideo", "width", width)
    cc.set_input(api, "WanImageToVideo", "height", height)
    cc.set_input(api, "WanImageToVideo", "length", length)
    cc.set_input(api, "VHS_VideoCombine", "filename_prefix", out_prefix)
    files, hist = cc.generate(api, timeout=timeout)
    if not files:
        raise RuntimeError(f"segment produced no video; history={json.dumps(hist)[:600]}")
    return files[0]


def run_t2v_seed(prompt, negative, length, width, height, out_prefix, timeout):
    if not os.path.exists(DEFAULT_T2V_WF):
        raise SystemExit(f"T2V template not found: {DEFAULT_T2V_WF}\n"
                         "Use --seed-image instead.")
    api = load_graph(DEFAULT_T2V_WF)
    pos, neg = resolve_prompt_nodes(api)
    if prompt is not None and pos:
        api[pos]["inputs"]["text"] = prompt
    if negative is not None and neg:
        api[neg]["inputs"]["text"] = negative
    for latent in ("Wan22ImageToVideoLatent", "EmptyHunyuanLatentVideo"):
        cc.set_input(api, latent, "length", length)
        cc.set_input(api, latent, "width", width)
        cc.set_input(api, latent, "height", height)
    files, hist = cc.generate(api, timeout=timeout)
    if not files:
        raise RuntimeError(f"T2V seed produced no video; history={json.dumps(hist)[:600]}")
    return files[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--negative", default=None)
    seed = ap.add_mutually_exclusive_group(required=True)
    seed.add_argument("--seed-image", help="clean first frame (BEST anatomy for NSFW)")
    seed.add_argument("--seed-t2v", action="store_true",
                      help="generate segment 1 via T2V (anatomy lottery)")
    ap.add_argument("--workflow", default=DEFAULT_I2V_WF, help="I2V workflow json")
    ap.add_argument("--length", type=int, default=81, help="frames per segment")
    ap.add_argument("--segments", type=int, default=None, help="number of segments")
    ap.add_argument("--duration", type=float, default=None,
                    help="target seconds (computes --segments from --length)")
    ap.add_argument("--width", type=int, default=720)
    ap.add_argument("--height", type=int, default=1280)
    ap.add_argument("--out", default=None, help="final stitched mp4")
    ap.add_argument("--interp-fps", type=int, default=None,
                    help="motion-interpolate the final clip to this fps")
    ap.add_argument("--timeout", type=int, default=3600, help="per-segment seconds")
    ap.add_argument("--no-free", action="store_true",
                    help="don't POST /free at the end")
    args = ap.parse_args()

    # seconds -> segment count: seg1 = length frames, each next adds length-1 (seam dropped)
    if args.duration is not None:
        target = int(round(args.duration * FPS))
        n = max(1, 1 + -(-(target - args.length) // (args.length - 1)))  # ceil
        args.segments = n
    if not args.segments:
        args.segments = 6

    total_frames = args.length + (args.segments - 1) * (args.length - 1)
    log(f"plan: {args.segments} segments x {args.length}f @ {args.width}x{args.height} "
        f"-> ~{total_frames} frames / {total_frames / FPS:.1f}s @ {FPS}fps")
    log("reminder: free Ollama (sudo systemctl restart ollama.service) before a heavy "
        "chain — VRAM contention forces offload thrash.")

    run_id = int(time.time())
    out_dir = os.path.join(cc.OUTPUT_DIR, f"chain_{run_id}")
    os.makedirs(out_dir, exist_ok=True)
    final = args.out or os.path.join(out_dir, f"chain_{run_id}.mp4")
    segment_videos = []

    try:
        # ---- seed: establish segment 1 + its last frame as the first init ----
        if args.seed_t2v:
            log("seed: T2V (anatomy lottery — prefer --seed-image for NSFW)")
            seg = run_t2v_seed(args.prompt, args.negative, args.length,
                               args.width, args.height,
                               f"chain_{run_id}/seg_000", args.timeout)
            segment_videos.append(seg)
            log(f"  seg 1/{args.segments} -> {os.path.basename(seg)}")
            first_done = 1
            init_png = os.path.join(INPUT_DIR, f"chain_{run_id}_000.png")
            extract_last_frame(seg, init_png)
        else:
            if not os.path.exists(args.seed_image):
                raise SystemExit(f"--seed-image not found: {args.seed_image}")
            init_png = os.path.join(INPUT_DIR, f"chain_{run_id}_000.png")
            shutil.copy(args.seed_image, init_png)
            first_done = 0

        # ---- chain the remaining I2V segments ----
        for i in range(first_done, args.segments):
            init_name = os.path.basename(init_png)  # LoadImage reads from ComfyUI/input/
            log(f"  seg {i + 1}/{args.segments}: I2V from {init_name}")
            seg = run_i2v_segment(args.workflow, init_name, args.prompt, args.negative,
                                  args.length, args.width, args.height,
                                  f"chain_{run_id}/seg_{i:03d}", args.timeout)
            segment_videos.append(seg)
            log(f"    -> {os.path.basename(seg)}")
            if i + 1 < args.segments:
                init_png = os.path.join(INPUT_DIR, f"chain_{run_id}_{i + 1:03d}.png")
                extract_last_frame(seg, init_png)

        # ---- stitch (drop the duplicated seam frame on segments 2..N) ----
        log(f"stitching {len(segment_videos)} segments")
        norm = []
        for idx, seg in enumerate(segment_videos):
            np_ = os.path.join(out_dir, f"norm_{idx:03d}.mp4")
            normalize(seg, np_, drop_first=(idx > 0))
            norm.append(np_)
        concat(norm, final)

        if args.interp_fps:
            interp_out = final.replace(".mp4", f"_interp{args.interp_fps}.mp4")
            log(f"interpolating -> {args.interp_fps}fps")
            interpolate(final, interp_out, args.interp_fps)
            final = interp_out

    except TimeoutError:
        interrupt()
        log("TIMEOUT — server job interrupted. If the queue looks stuck, run: "
            "systemctl --user restart comfyui.service")
        raise
    finally:
        if not args.no_free:
            cc.free_vram()

    log(f"DONE: {final}")
    print(final)


if __name__ == "__main__":
    main()
