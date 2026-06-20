#!/usr/bin/env python3
"""Gentle coexistence probe — force ONE small image checkpoint resident in ComfyUI.

Phase-A feasibility (AgentOS). Submits the MINIMAL SD txt2img graph against the
SMALLEST reliable image checkpoint (sd_turbo, ~4.86 GiB on disk) at low res / 1 step
so ComfyUI loads it into VRAM and (with --disable-smart-memory) keeps it resident.
Reports VRAM before/after via ComfyUI's read-only /system_stats so YOU can read the
delta the checkpoint cost.

Reuses the proven client at ../dreaming/comfy_client.py (submit/wait/generate), exactly
like ../dreaming/test_aio.py builds a direct {class_type, inputs} graph (no template).

sd_turbo is SD2.1-Turbo: CheckpointLoaderSimple yields MODEL(0)+CLIP(1)+VAE(2), built
for very low step counts. 1 step at 512x512 is intentionally trivial — the point is
residency, not image quality.

RUN IT YOURSELF (do not auto-run):
    python3 spikes/ue-probe/comfy_load_small.py
Options:
    --ckpt FILE   checkpoint name as ComfyUI sees it (default sd_turbo.safetensors)
    --w / --h     latent size (default 512x512)
    --steps N     sampler steps (default 1 — sd_turbo is a turbo model)
    --cfg F       CFG (default 1.0 — turbo)
    --keep        after gen, do NOT free; leave the model resident (DEFAULT here)
    --free        instead, free VRAM at the end (release the checkpoint)
    --dump        print the graph JSON and exit (no submit)
"""
import argparse
import os
import sys
import time

# import the proven client from ../dreaming without copying it
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dreaming"))
import comfy_client as c  # noqa: E402


def vram_snapshot(tag):
    """Read-only ComfyUI /system_stats — print used/free VRAM for the cuda:0 device."""
    try:
        stats = c._get("/system_stats")
    except Exception as e:
        print(f"[vram:{tag}] /system_stats unreachable: {e}")
        return None
    dev = (stats.get("devices") or [{}])[0]
    total = dev.get("vram_total", 0)
    free = dev.get("vram_free", 0)
    used = total - free
    mib = 1024 * 1024
    print(f"[vram:{tag}] device={dev.get('name','?')} "
          f"used={used/mib:7.0f} MiB  free={free/mib:7.0f} MiB  "
          f"total={total/mib:7.0f} MiB")
    return used


def build(a):
    """Minimal SD txt2img graph: ckpt -> CLIP(pos/neg) + EmptyLatent -> KSampler ->
    VAEDecode -> SaveImage. SaveImage makes it a valid headless output node."""
    g = {}
    g["ckpt"] = {"class_type": "CheckpointLoaderSimple",
                 "inputs": {"ckpt_name": a.ckpt}}
    g["pos"] = {"class_type": "CLIPTextEncode",
                "inputs": {"clip": ["ckpt", 1], "text": a.prompt}}
    g["neg"] = {"class_type": "CLIPTextEncode",
                "inputs": {"clip": ["ckpt", 1], "text": ""}}
    g["lat"] = {"class_type": "EmptyLatentImage",
                "inputs": {"width": a.w, "height": a.h, "batch_size": 1}}
    g["ks"] = {"class_type": "KSampler",
               "inputs": {"model": ["ckpt", 0], "positive": ["pos", 0],
                          "negative": ["neg", 0], "latent_image": ["lat", 0],
                          "seed": a.seed, "steps": a.steps, "cfg": a.cfg,
                          "sampler_name": a.sampler, "scheduler": a.scheduler,
                          "denoise": 1.0}}
    g["dec"] = {"class_type": "VAEDecode",
                "inputs": {"samples": ["ks", 0], "vae": ["ckpt", 2]}}
    g["save"] = {"class_type": "SaveImage",
                 "inputs": {"images": ["dec", 0], "filename_prefix": a.prefix}}
    return g


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", default="a calm landscape, soft light")
    p.add_argument("--ckpt", default="sd_turbo.safetensors")
    p.add_argument("--w", type=int, default=512)
    p.add_argument("--h", type=int, default=512)
    p.add_argument("--steps", type=int, default=1)
    p.add_argument("--cfg", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--sampler", default="euler")
    p.add_argument("--scheduler", default="normal")
    p.add_argument("--prefix", default="ueprobe_small")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--keep", action="store_true", default=True,
                   help="leave the checkpoint resident after gen (default)")
    p.add_argument("--free", action="store_true",
                   help="free VRAM at the end instead of keeping resident")
    p.add_argument("--dump", action="store_true")
    a = p.parse_args()

    g = build(a)
    if a.dump:
        import json
        print(json.dumps(g, indent=1))
        return

    print(f"[probe] checkpoint = {a.ckpt}  {a.w}x{a.h}  steps={a.steps}  cfg={a.cfg}")
    vram_snapshot("before")
    t0 = time.time()
    try:
        files, _hist = c.generate(g, timeout=a.timeout)
    except Exception as e:
        print(f"[probe] generate FAILED: {e}")
        vram_snapshot("after-fail")
        raise
    dt = time.time() - t0
    print(f"[probe] done in {dt:.1f}s -> {files}")
    print(f"[probe] >>> {a.ckpt} is now RESIDENT in ComfyUI VRAM <<<")
    used = vram_snapshot("after")
    if used is not None:
        print(f"[probe] sample ComfyUI VRAM used now: {used/(1024*1024):.0f} MiB "
              f"(includes the checkpoint + any other tenant on the card)")

    if a.free:
        ok = c.free_vram()
        print(f"[probe] /free -> {'ok' if ok else 'FAILED'} (checkpoint released)")
        vram_snapshot("after-free")
    else:
        print("[probe] keeping checkpoint resident (no /free). "
              "Run `python3 ../dreaming/comfy_client.py free` to release it later.")


if __name__ == "__main__":
    main()
