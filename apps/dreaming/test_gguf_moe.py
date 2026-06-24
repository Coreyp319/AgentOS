#!/usr/bin/env python3
"""Text-to-video on a Wan 2.2 dual-expert (high+low noise) GGUF model set,
non-distilled (no Lightning) -> real CFG + ~20 steps for best anatomy.

Builds the MoE graph directly: two UnetLoaderGGUF experts, each with its own
ModelSamplingSD3 -> KSamplerAdvanced, split at a step boundary; shared umt5 CLIP
+ Wan VAE. Use for the Enhanced-NSFW (nolightning) GGUF.

Usage:
  test_gguf_moe.py --prompt "..." --high HIGH.gguf --low LOW.gguf
     [--neg ...] [--w 576 --h 1024 --length 81] [--steps 20 --boundary 10]
     [--cfg 3.5 --shift 8.0 --seed 1 --fps 16] [--prefix enh_nsfw] [--dump]
"""
import argparse
import time

import comfy_client as c

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
        "lat": {"class_type": "EmptyHunyuanLatentVideo",
                "inputs": {"width": a.w, "height": a.h, "length": a.length, "batch_size": 1}},
        "mh": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["uh", 0], "shift": a.shift}},
        "ml": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["ul", 0], "shift": a.shift}},
        "kh": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["mh", 0], "add_noise": "enable", "noise_seed": a.seed,
                          "steps": a.steps, "cfg": a.cfg, "sampler_name": a.sampler,
                          "scheduler": a.scheduler, "positive": ["pos", 0], "negative": ["neg", 0],
                          "latent_image": ["lat", 0], "start_at_step": 0,
                          "end_at_step": a.boundary, "return_with_leftover_noise": "enable"}},
        "kl": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["ml", 0], "add_noise": "disable", "noise_seed": a.seed,
                          "steps": a.steps, "cfg": a.cfg, "sampler_name": a.sampler,
                          "scheduler": a.scheduler, "positive": ["pos", 0], "negative": ["neg", 0],
                          "latent_image": ["kh", 0], "start_at_step": a.boundary,
                          "end_at_step": 10000, "return_with_leftover_noise": "disable"}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["kl", 0], "vae": ["vae", 0]}},
        "vid": {"class_type": "CreateVideo", "inputs": {"images": ["dec", 0], "fps": a.fps}},
        "save": {"class_type": "SaveVideo",
                 "inputs": {"video": ["vid", 0], "filename_prefix": a.prefix,
                            "format": "auto", "codec": "auto"}},
    }
    return g


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", required=True)
    p.add_argument("--neg", default=DEFAULT_NEG)
    p.add_argument("--high", required=True)
    p.add_argument("--low", required=True)
    p.add_argument("--clip", default="umt5_xxl_fp8_e4m3fn_scaled.safetensors")
    p.add_argument("--vae", default="wan_2.1_vae.safetensors")
    p.add_argument("--w", type=int, default=576)
    p.add_argument("--h", type=int, default=1024)
    p.add_argument("--length", type=int, default=81)
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--boundary", type=int, default=10)
    p.add_argument("--cfg", type=float, default=3.5)
    p.add_argument("--shift", type=float, default=8.0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--fps", type=int, default=16)
    p.add_argument("--sampler", default="euler")
    p.add_argument("--scheduler", default="beta")
    p.add_argument("--prefix", default="enh_nsfw")
    p.add_argument("--timeout", type=int, default=2400)
    p.add_argument("--dump", action="store_true")
    a = p.parse_args()
    g = build(a)
    if a.dump:
        import json
        print(json.dumps(g, indent=1))
        return
    t0 = time.time()
    files, _ = c.generate(g, timeout=a.timeout)
    print(f"[done] {time.time()-t0:.0f}s -> {files}")


if __name__ == "__main__":
    main()
