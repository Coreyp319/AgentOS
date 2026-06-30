#!/usr/bin/env python3
"""Text-to-opening — generate a dream's opening frame from a text prompt (ADR-0015 seed source).

The other "real seed source" beside upload: instead of *your photo*, describe the opening and a
fast t2i model (SD-Turbo) renders it. The flow is safety-equal to upload, because a t2i CAN render
a person:
  1. the prompt passes the deterministic red-line gate (lucid_safety.gate_prompt);
  2. one image is generated via ComfyUI (cc.submit/wait/output_files);
  3. the generated frame is returned and goes through B2 + start() like ANY seed.

ComfyUI is the running instance (the opening is a light one-shot; the heavy i2v video is the leased
part). Test seam: LUCID_T2I_CMD stubs generation so the prompt gate is provable without a GPU.
"""
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import comfy_client as cc   # noqa: E402  (the one validated ComfyUI client — ADR-0001)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_safety as S    # noqa: E402  (the red-line prompt gate)
import lucid_models         # noqa: E402  (model registry — single source of truth)

CKPT = os.environ.get("LUCID_T2I_CKPT") or lucid_models.get("t2i-opening", "sd_turbo.safetensors")
EST_MIB = int(os.environ.get("LUCID_T2I_EST_MIB", "8500"))   # SDXL/Illustrious weights + working set
STEPS = int(os.environ.get("LUCID_T2I_STEPS", "28"))   # Illustrious (SDXL): ~25-30 steps (not turbo)
CFG = float(os.environ.get("LUCID_T2I_CFG", "5.0"))    # Illustrious (SDXL): cfg ~5
NEG = ("worst quality, low quality, jpeg artifacts, text, watermark, logo, blurry, "
       "deformed, bad anatomy, extra limbs, extra fingers, fused fingers, mutated hands")
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# --- Krea 2 opening backend (ADR-0055) ------------------------------------------------------
# A fast (12-step, cfg 1) higher-quality turbo opener, selectable via the `t2i-engine` registry
# selector or LUCID_T2I_ENGINE. The text encoder is RATING-GATED: the stock Qwen3-VL for sfw
# dreams, the abliterated Qwen3-VL for the mature lane — so a mature opening can be pose/anatomy
# correct the way the SDXL opener is, without uncensoring sfw dreams. Default engine stays
# 'illustrious' (divingIllustrious): nothing changes until flipped.
KREA_UNET = os.environ.get("LUCID_T2I_KREA_UNET", "Krea/krea2_turbo_fp8_scaled.safetensors")
KREA_VAE = os.environ.get("LUCID_T2I_KREA_VAE", "qwen_image_vae.safetensors")
KREA_ENC_SFW = os.environ.get("LUCID_T2I_KREA_ENC_SFW", "qwen3vl_4b_fp8_scaled.safetensors")
KREA_ENC_MATURE = os.environ.get(
    "LUCID_T2I_KREA_ENC_MATURE", "Huihui-Qwen3-VL-4B-Instruct-abliterated-fp8_scaled.safetensors")
KREA_STEPS = int(os.environ.get("LUCID_T2I_KREA_STEPS", "12"))   # turbo: 8-12 steps
KREA_CFG = float(os.environ.get("LUCID_T2I_KREA_CFG", "1.0"))    # distilled -> cfg 1 (no real negative)
KREA_EST_MIB = int(os.environ.get("LUCID_T2I_KREA_EST_MIB", "18500"))  # 13GB DiT + ~5GB Qwen3-VL enc + working set


def _free_vram_mib():
    """Read-only free-VRAM probe (predict-before-load). None if unreadable -> caller fails open."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return None


def _workflow(prompt, seed, w, h):
    """A minimal native-node t2i graph in ComfyUI API format (no blueprint needed)."""
    return {
        "ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["ckpt", 1]}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG, "clip": ["ckpt", 1]}},
        "lat": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "smp": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": STEPS, "cfg": CFG, "sampler_name": "euler_ancestral",
            "scheduler": "normal", "denoise": 1.0,
            "model": ["ckpt", 0], "positive": ["pos", 0], "negative": ["neg", 0], "latent_image": ["lat", 0]}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["smp", 0], "vae": ["ckpt", 2]}},
        "sav": {"class_type": "SaveImage", "inputs": {"filename_prefix": "lucid-opening", "images": ["dec", 0]}},
    }


def _t2i_engine():
    """Active opening-image backend: 'illustrious' (default, known-good SDXL) | 'krea2'. Flip via
    LUCID_T2I_ENGINE env or the registry 't2i-engine' selector (mirrors i2v-engine); default
    preserves prior behavior — nothing changes until flipped."""
    return (os.environ.get("LUCID_T2I_ENGINE")
            or lucid_models.get("t2i-engine", "illustrious")).strip().lower()


def _workflow_krea(prompt, seed, w, h, rating="sfw"):
    """Krea 2 Turbo t2i graph (ComfyUI API format). The text encoder is rating-gated: stock
    Qwen3-VL for sfw, abliterated for mature. cfg=1 (distilled) so the negative is a zeroed-out
    positive (no real negative at turbo cfg). Needs ComfyUI with the 'krea2' CLIP type."""
    enc = KREA_ENC_MATURE if rating == "mature" else KREA_ENC_SFW
    return {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": KREA_UNET, "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": enc, "type": "krea2", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": KREA_VAE}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["clip", 0]}},
        "neg": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["pos", 0]}},
        "lat": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "smp": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": KREA_STEPS, "cfg": KREA_CFG, "sampler_name": "euler",
            "scheduler": "beta", "denoise": 1.0,
            "model": ["unet", 0], "positive": ["pos", 0], "negative": ["neg", 0], "latent_image": ["lat", 0]}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["smp", 0], "vae": ["vae", 0]}},
        "sav": {"class_type": "SaveImage", "inputs": {"filename_prefix": "lucid-opening", "images": ["dec", 0]}},
    }


def generate_opening(prompt, out_path, w=768, h=1344, seed=None, timeout=180, rating="sfw"):   # 9:16 portrait (matches i2v frame); rating gates the krea2 encoder
    """Gate the prompt, render one t2i frame, copy it to out_path. Returns out_path.
    Raises ValueError if the prompt is red-lined, RuntimeError if generation fails."""
    gated = S.gate_prompt(prompt)
    if gated is None:
        raise ValueError("That description was blocked by the red-line gate.")
    cmd = os.environ.get("LUCID_T2I_CMD")
    if cmd:   # test seam: prove the gate + flow without a GPU (the stub writes out_path itself)
        if subprocess.run(cmd, shell=True).returncode != 0:
            raise RuntimeError("t2i stub failed")
        return out_path
    if seed is None:
        seed = random.randint(1, 2 ** 31)
    # Predict-before-load (read-only; never frees shared GPU state): refuse rather than OOM the
    # box when VRAM is contended. Fail-open if we can't read it.
    engine = _t2i_engine()
    est = KREA_EST_MIB if engine == "krea2" else EST_MIB
    free = _free_vram_mib()
    if free is not None and free < est:
        raise RuntimeError(f"not enough free GPU memory for the opening ({free} MiB free, "
                           f"need ~{est}) — try again when the GPU frees")
    api = _workflow_krea(gated, seed, w, h, rating) if engine == "krea2" else _workflow(gated, seed, w, h)
    pid, _client = cc.submit(api)          # submit() returns (prompt_id, client_id)
    hist = cc.wait(pid, timeout=timeout)
    imgs = [f for f in cc.output_files(hist) if f.lower().endswith(_IMG_EXTS)]
    if not imgs:
        raise RuntimeError("the opening image didn't render")
    import shutil
    shutil.copy(imgs[0], out_path)
    return out_path


# CLI: generate an opening to a path (for testing / the right-click "text" surface)
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Lucid text-to-opening (ADR-0015)")
    ap.add_argument("prompt")
    ap.add_argument("--out", default="/tmp/lucid-opening.png")
    ap.add_argument("--w", type=int, default=512)
    ap.add_argument("--h", type=int, default=512)
    a = ap.parse_args()
    try:
        p = generate_opening(a.prompt, a.out, a.w, a.h)
        print(f"opening -> {p}")
    except Exception as e:
        print(f"failed: {e}")
        sys.exit(1)
