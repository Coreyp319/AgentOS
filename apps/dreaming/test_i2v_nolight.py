#!/usr/bin/env python3
"""Image-to-video on a Wan 2.2 dual-expert GGUF set, NON-distilled (no Lightning)
-> real CFG + ~20 steps for proper anatomy. The I2V sibling of test_gguf_moe.py.

Built to answer one question first: does the Enhanced-NSFW *nolight* GGUF actually
do I2V (vs cream frames = T2V-only)? Mirrors the proven Remix I2V topology:
LoadImage -> WanImageToVideo (carries pos/neg cond + start image + latent) ->
two UnetLoaderGGUF experts each ModelSamplingSD3 -> KSamplerAdvanced, split at a
step boundary -> VAEDecode -> VHS_VideoCombine. Same node classes as the chain
workflow so chain_video.py can drive it unchanged.

  test_i2v_nolight.py --image images.jpg --prompt "..." \
     [--w 576 --h 1024 --length 49 --steps 20 --boundary 10 --cfg 3.5]
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comfy_client as c

HIGH = "wan22_enhNSFW_nolight_cf_Q6K_high.gguf"
LOW = "wan22_enhNSFW_nolight_cf_Q6K_low.gguf"
DEFAULT_NEG = ("blurry, low quality, distorted face, bad anatomy, extra limbs, "
               "extra fingers, fused fingers, deformed, disfigured, malformed limbs, "
               "mutated hands, missing fingers, poorly drawn hands, poorly drawn face, "
               "duplicate limbs, merged bodies, watermark, text, plastic skin, wax face, cgi")


def build(a):
    g = {
        "uh": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": a.high}},
        "ul": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": a.low}},
        "clip": {"class_type": "CLIPLoader",
                 "inputs": {"clip_name": a.clip, "type": "wan", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": a.vae}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": a.prompt}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": a.neg}},
        "img": {"class_type": "LoadImage", "inputs": {"image": a.image}},
        "i2v": {"class_type": "WanImageToVideo",
                "inputs": {"width": a.w, "height": a.h, "length": a.length, "batch_size": 1,
                           "positive": ["pos", 0], "negative": ["neg", 0],
                           "vae": ["vae", 0], "start_image": ["img", 0]}},
        "mh": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["uh", 0], "shift": a.shift}},
        "ml": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["ul", 0], "shift": a.shift}},
        "kh": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["mh", 0], "add_noise": "enable", "noise_seed": a.seed,
                          "steps": a.steps, "cfg": a.cfg, "sampler_name": a.sampler,
                          "scheduler": a.scheduler, "positive": ["i2v", 0], "negative": ["i2v", 1],
                          "latent_image": ["i2v", 2], "start_at_step": 0,
                          "end_at_step": a.boundary, "return_with_leftover_noise": "enable"}},
        "kl": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["ml", 0], "add_noise": "disable", "noise_seed": a.seed,
                          "steps": a.steps, "cfg": a.cfg, "sampler_name": a.sampler,
                          "scheduler": a.scheduler, "positive": ["i2v", 0], "negative": ["i2v", 1],
                          "latent_image": ["kh", 0], "start_at_step": a.boundary,
                          "end_at_step": 10000, "return_with_leftover_noise": "disable"}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["kl", 0], "vae": ["vae", 0]}},
        "vid": {"class_type": "VHS_VideoCombine",
                "inputs": {"images": ["dec", 0], "frame_rate": a.fps, "loop_count": 0,
                           "filename_prefix": a.prefix, "format": "video/h264-mp4",
                           "pix_fmt": "yuv420p", "crf": 19, "save_metadata": True,
                           "pingpong": False, "save_output": True}},
    }
    # optional realism LoRA on each expert — rewire each ModelSamplingSD3 through LoraLoaderModelOnly.
    # NOTE: Instareal is a *realism* LoRA (skin/photoreal), not an explicit-anatomy LoRA — it may sharpen
    # the body but won't itself add a detail the base weights don't know. (skill §NSFW recipe, camp A/B)
    # Per-expert strength: an explicit-anatomy LoRA on the HIGH expert (which lays out bodies) melts
    # anatomy, so the default keeps high=0.0 / low=0.6. --lora-strength, if given, overrides both.
    s_high = a.lora_strength if a.lora_strength is not None else a.lora_strength_high
    s_low = a.lora_strength if a.lora_strength is not None else a.lora_strength_low
    if a.lora_high and s_high:
        g["lh"] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"model": ["uh", 0], "lora_name": a.lora_high,
                              "strength_model": s_high}}
        g["mh"]["inputs"]["model"] = ["lh", 0]
    if a.lora_low and s_low:
        g["ll"] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"model": ["ul", 0], "lora_name": a.lora_low,
                              "strength_model": s_low}}
        g["ml"]["inputs"]["model"] = ["ll", 0]
    return g


def blank_check(video_path):
    """We can't see frames; flag a cream/blank render objectively. Pull the middle
    frame and read its per-channel std-dev — a flat latent is near-zero variance."""
    mid = video_path + ".mid.png"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", video_path, "-vf", "select='eq(n\\,12)'", "-vframes", "1", mid],
                   check=False)
    if not os.path.exists(mid):
        return "unknown (no mid frame)"
    out = subprocess.run(["identify", "-format", "%[standard-deviation]", mid],
                         capture_output=True, text=True).stdout.strip()
    try:
        sd = float(out.split()[0]) if out else 0.0
    except ValueError:
        sd = 0.0
    # 8-bit std-dev: <~600 (of 65535) ~ nearly flat. Normal photo content is much higher.
    verdict = "LIKELY BLANK/CREAM" if sd < 800 else "has real content"
    return f"mid-frame std-dev={sd:.0f} -> {verdict}  ({mid})"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image", required=True, help="filename in ComfyUI/input/")
    p.add_argument("--prompt", required=True)
    p.add_argument("--neg", default=DEFAULT_NEG)
    p.add_argument("--high", default=HIGH)
    p.add_argument("--low", default=LOW)
    p.add_argument("--clip", default="nsfw_wan_umt5-xxl_fp8_scaled.safetensors")
    p.add_argument("--vae", default="wan_2.1_vae.safetensors")
    p.add_argument("--lora-high", dest="lora_high", default=None, help="realism LoRA on the high expert")
    p.add_argument("--lora-low", dest="lora_low", default=None, help="realism LoRA on the low expert")
    p.add_argument("--lora-strength", dest="lora_strength", type=float, default=None,
                   help="override: same strength on BOTH experts (else use the per-expert defaults)")
    p.add_argument("--lora-strength-high", dest="lora_strength_high", type=float, default=0.0,
                   help="high-noise expert lays out bodies — keep an anatomy LoRA off it (0.0)")
    p.add_argument("--lora-strength-low", dest="lora_strength_low", type=float, default=0.6)
    p.add_argument("--w", type=int, default=576)
    p.add_argument("--h", type=int, default=1024)
    p.add_argument("--length", type=int, default=49)
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--boundary", type=int, default=10)
    p.add_argument("--cfg", type=float, default=3.5)
    p.add_argument("--shift", type=float, default=8.0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--fps", type=int, default=16)
    p.add_argument("--sampler", default="euler")
    p.add_argument("--scheduler", default="beta")
    p.add_argument("--prefix", default="i2v_nolight_test")
    p.add_argument("--timeout", type=int, default=2400)
    p.add_argument("--dump", action="store_true")
    a = p.parse_args()

    g = build(a)
    if a.dump:
        print(json.dumps(g, indent=2))
        return
    print(f"[test] non-distilled I2V: {a.w}x{a.h}/{a.length}f steps={a.steps} "
          f"boundary={a.boundary} cfg={a.cfg} from {a.image}", flush=True)
    files, hist = c.generate(g, timeout=a.timeout)
    if not files:
        raise SystemExit(f"no output; history={json.dumps(hist)[:800]}")
    out = files[0]
    print(f"[test] OUTPUT: {out}", flush=True)
    print(f"[test] {blank_check(out)}", flush=True)
    c.free_vram()


if __name__ == "__main__":
    main()
