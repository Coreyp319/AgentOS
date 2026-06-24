#!/usr/bin/env python3
"""Run a text-to-video gen against an all-in-one Wan 2.2 checkpoint
(e.g. Phr00t Rapid-AIO NSFW). Builds a minimal checkpoint->T2V graph directly
(CheckpointLoaderSimple has CLIP+VAE baked) — no template/subgraph needed.

Usage:
  test_aio.py --prompt "..." [--neg "..."] [--ckpt FILE] [--lora FILE --lora-str 0.8]
              [--w 720 --h 1280 --length 81 --steps 6 --cfg 1.0 --shift 8.0]
              [--seed N] [--fps 16] [--prefix aio_test] [--dump]
"""
import argparse
import time

import comfy_client as c

DEFAULT_NEG = ("blurry, low quality, distorted face, bad anatomy, extra limbs, "
               "extra fingers, fused fingers, deformed, disfigured, malformed limbs, "
               "mutated hands, missing fingers, poorly drawn hands, poorly drawn face, "
               "duplicate limbs, merged bodies, watermark, text, plastic skin, wax face, cgi")


def build(a):
    g = {}
    g["ckpt"] = {"class_type": "CheckpointLoaderSimple",
                 "inputs": {"ckpt_name": a.ckpt}}
    model = ["ckpt", 0]
    if a.lora:
        g["lora"] = {"class_type": "LoraLoaderModelOnly",
                     "inputs": {"model": model, "lora_name": a.lora,
                                "strength_model": a.lora_str}}
        model = ["lora", 0]
    g["msd3"] = {"class_type": "ModelSamplingSD3",
                 "inputs": {"model": model, "shift": a.shift}}
    g["pos"] = {"class_type": "CLIPTextEncode",
                "inputs": {"clip": ["ckpt", 1], "text": a.prompt}}
    g["neg"] = {"class_type": "CLIPTextEncode",
                "inputs": {"clip": ["ckpt", 1], "text": a.neg}}
    g["lat"] = {"class_type": "Wan22ImageToVideoLatent",
                "inputs": {"vae": ["ckpt", 2], "width": a.w, "height": a.h,
                           "length": a.length, "batch_size": 1}}
    g["ks"] = {"class_type": "KSampler",
               "inputs": {"model": ["msd3", 0], "positive": ["pos", 0],
                          "negative": ["neg", 0], "latent_image": ["lat", 0],
                          "seed": a.seed, "steps": a.steps, "cfg": a.cfg,
                          "sampler_name": a.sampler, "scheduler": a.scheduler,
                          "denoise": 1.0}}
    g["dec"] = {"class_type": "VAEDecode",
                "inputs": {"samples": ["ks", 0], "vae": ["ckpt", 2]}}
    g["vid"] = {"class_type": "CreateVideo",
                "inputs": {"images": ["dec", 0], "fps": a.fps}}
    g["save"] = {"class_type": "SaveVideo",
                 "inputs": {"video": ["vid", 0], "filename_prefix": a.prefix,
                            "format": "auto", "codec": "auto"}}
    return g


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", required=True)
    p.add_argument("--neg", default=DEFAULT_NEG)
    p.add_argument("--ckpt", default="wan2.2-rapid-mega-aio-nsfw-v12.2.safetensors")
    p.add_argument("--lora", default=None)
    p.add_argument("--lora-str", type=float, default=0.8, dest="lora_str")
    p.add_argument("--w", type=int, default=720)
    p.add_argument("--h", type=int, default=1280)
    p.add_argument("--length", type=int, default=81)
    p.add_argument("--steps", type=int, default=6)
    p.add_argument("--cfg", type=float, default=1.0)
    p.add_argument("--shift", type=float, default=8.0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--fps", type=int, default=16)
    p.add_argument("--sampler", default="dpmpp_sde")
    p.add_argument("--scheduler", default="beta")
    p.add_argument("--prefix", default="aio_test")
    p.add_argument("--timeout", type=int, default=1800)
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
