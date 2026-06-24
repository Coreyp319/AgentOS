#!/usr/bin/env python3
"""Reference image -> painterly look (the dreamTex), via local ComfyUI img2img.

ADR-0023 requirement (2026-06-18): the creative-env workflow MUST use a reference
image. The model no longer guesses the look from text — it proposes the look
CONDITIONED on a real reference, so golden-hour light, palette, composition and
depth come FROM the image instead of being approximated procedurally.

This is the "reference -> look" stage: SDXL img2img. Initialize the latent from
the reference, denoise it through a painterly prompt, so the result keeps the
reference's composition + lighting and repaints it as an oil painting. The output
is the `dreamTex` the live windable shader warps with wind (ADR-0009 dream-as-
texture: model proposes the look, code disposes the motion).

Local-first: runs on the local ComfyUI (no cloud). Reuses the proven
apps/dreaming/comfy_client.py HTTP primitives (don't reinvent).

  python3 ref_to_look.py <reference.jpg> [--out out/ref_look.png]
      [--denoise 0.58] [--ckpt ...] [--prompt ...] [--seed N]
"""
import argparse
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dreaming"))
import comfy_client as cc  # noqa: E402  (path-injected sibling spike)

COMFY_INPUT = os.path.join(cc.COMFY_ROOT, "input")

# A painterly-landscape SDXL checkpoint present on this box. img2img keeps the
# reference composition; the prompt pushes it toward oil paint.
DEFAULT_CKPT = "divingIllustriousReal_v40VAE.safetensors"
DEFAULT_POS = (
    "oil painting, impressionist landscape, golden hour wheat field at sunset, "
    "low warm sun glowing on the horizon, backlit translucent wheat heads, "
    "rolling tuscan hills, a distant farmhouse on a hill, soft atmospheric haze, "
    "visible painterly brush strokes, impasto, warm amber gold and bronze palette "
    "with cool distant hills, shallow depth of field, serene, masterpiece"
)
DEFAULT_NEG = (
    "photograph, photo, realistic, 3d render, cgi, flat, posterized, low-poly, "
    "hard edges, blurry, lowres, deformed, text, watermark, signature, oversaturated"
)


def build_img2img(image_name, ckpt, pos, neg, seed, steps, cfg, denoise, w, h,
                  lora=None, lora_strength=1.0):
    """A standard SDXL img2img /prompt API graph (hand-built, no UI conversion).

    An optional style LoRA (e.g. an oil-painting slider) is inserted between the
    checkpoint and the CLIP/sampler so the MEDIUM (brushwork) is pushed without a
    painterly base checkpoint. The reference still drives composition + light."""
    model_src, clip_src = ["ckpt", 0], ["ckpt", 1]
    g = {
        "ckpt": {"class_type": "CheckpointLoaderSimple",
                 "inputs": {"ckpt_name": ckpt}},
        "load": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "scale": {"class_type": "ImageScale",
                  "inputs": {"image": ["load", 0], "width": w, "height": h,
                             "upscale_method": "lanczos", "crop": "center"}},
    }
    if lora:
        g["lora"] = {"class_type": "LoraLoader",
                     "inputs": {"model": ["ckpt", 0], "clip": ["ckpt", 1],
                                "lora_name": lora,
                                "strength_model": lora_strength,
                                "strength_clip": lora_strength}}
        model_src, clip_src = ["lora", 0], ["lora", 1]
    g["pos"] = {"class_type": "CLIPTextEncode",
                "inputs": {"clip": clip_src, "text": pos}}
    g["neg"] = {"class_type": "CLIPTextEncode",
                "inputs": {"clip": clip_src, "text": neg}}
    g["enc"] = {"class_type": "VAEEncode",
                "inputs": {"pixels": ["scale", 0], "vae": ["ckpt", 2]}}
    g["ks"] = {"class_type": "KSampler",
               "inputs": {"model": model_src, "positive": ["pos", 0],
                          "negative": ["neg", 0], "latent_image": ["enc", 0],
                          "seed": seed, "steps": steps, "cfg": cfg,
                          "sampler_name": "dpmpp_2m", "scheduler": "karras",
                          "denoise": denoise}}
    g["dec"] = {"class_type": "VAEDecode",
                "inputs": {"samples": ["ks", 0], "vae": ["ckpt", 2]}}
    g["save"] = {"class_type": "SaveImage",
                 "inputs": {"images": ["dec", 0], "filename_prefix": "ref_look"}}
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference", help="path to the reference image")
    ap.add_argument("--ckpt", default=DEFAULT_CKPT)
    ap.add_argument("--prompt", default=DEFAULT_POS)
    ap.add_argument("--negative", default=DEFAULT_NEG)
    ap.add_argument("--denoise", type=float, default=0.58,
                    help="0=keep reference exactly, 1=ignore it. ~0.5-0.65 repaints.")
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--cfg", type=float, default=5.5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=576)
    ap.add_argument("--out", default=None, help="copy the result here")
    ap.add_argument("--lora", default=None,
                    help="style LoRA filename in ComfyUI/models/loras (e.g. an oil-paint slider)")
    ap.add_argument("--lora-strength", type=float, default=1.0)
    ap.add_argument("--no-free", action="store_true",
                    help="don't free VRAM first")
    args = ap.parse_args()

    if not os.path.isfile(args.reference):
        raise SystemExit(f"reference not found: {args.reference}")
    os.makedirs(COMFY_INPUT, exist_ok=True)
    name = "ref_" + os.path.basename(args.reference)
    shutil.copy(args.reference, os.path.join(COMFY_INPUT, name))

    if not args.no_free:
        cc.free_vram()  # clear any resident model so SDXL has headroom

    api = build_img2img(name, args.ckpt, args.prompt, args.negative,
                        args.seed, args.steps, args.cfg, args.denoise,
                        args.width, args.height, args.lora, args.lora_strength)
    print(f"[ref_to_look] img2img denoise={args.denoise} steps={args.steps} "
          f"ckpt={args.ckpt}")
    t0 = time.time()
    pid, _ = cc.submit(api)
    hist = cc.wait(pid, timeout=600)
    status = hist.get("status", {})
    if status.get("status_str") == "error":
        raise SystemExit(f"generation errored: {status}")
    imgs = [p for p in cc.output_files(hist) if p.lower().endswith(".png")]
    print(f"[ref_to_look] {time.time()-t0:.0f}s -> {imgs}")
    if args.out and imgs:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        shutil.copy(imgs[0], args.out)
        print(f"[ref_to_look] copied -> {args.out}")


if __name__ == "__main__":
    main()
