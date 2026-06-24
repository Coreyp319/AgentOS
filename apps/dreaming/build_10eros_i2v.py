#!/usr/bin/env python3
"""Build a 10Eros (LTX-2.3) I2V api graph by cloning ComfyUI's own known-good
`video_ltx2_i2v_distilled` template and swapping its 3 bundled loaders
(CheckpointLoaderSimple / LTXAVTextEncoderLoader / LTXVAudioVAELoader) for the
GGUF-route loaders the installed pack uses:

  UnetLoaderGGUF      <- 10Eros GGUF transformer (model)
  DualCLIPLoaderGGUF  <- [gemma TE, ltx-2.3 text projection, "ltxv"]  (clip)
  VAELoaderKJ x2      <- LTX23 video VAE / audio VAE

Everything else (the dual-stage LTX sampler, img-to-video inplace, AV latents)
is left exactly as ComfyUI ships it. Writes workflows/10eros-i2v.api.json.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comfy_client as cc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = next((p for p in [
    os.path.expanduser("~/ComfyUI/.venv/lib/python3.12/site-packages/"
                        "comfyui_workflow_templates_media_image/templates/"
                        "video_ltx2_i2v_distilled.json"),
] if os.path.exists(p)), None)

UNET_DIR = os.path.expanduser("~/ComfyUI/models/unet")
GGUF_PREFER = ["10Eros_v1-Q6_K.gguf", "10Eros_v1-Q4_K_M.gguf"]  # Q6 = better anatomy
GEMMA_TE = "gemma-3-12b-it-ablit-norms-biproj-fp8mixed.safetensors"


def _pick_gguf():
    for f in GGUF_PREFER:
        p = os.path.join(UNET_DIR, f)
        # require a plausibly-complete file (>10 GB) so a half-downloaded Q6 is skipped
        if os.path.exists(p) and os.path.getsize(p) > 10 * 1024**3:
            return f
    return GGUF_PREFER[-1]
TEXT_PROJ = "ltx-2.3_text_projection_bf16.safetensors"
VIDEO_VAE = "LTX23_video_vae_bf16.safetensors"
AUDIO_VAE = "LTX23_audio_vae_bf16.safetensors"


def _refs(n):
    return [(k, v) for k, v in n["inputs"].items()
            if isinstance(v, list) and len(v) == 2 and isinstance(v[1], int)]


def _bypass_class(api, ct):
    """Drop nodes of class `ct`, rewiring their consumers to the node's own
    same-typed input source (pass-through). Used for redundant resize nodes."""
    for nid in [i for i, n in api.items() if n["class_type"] == ct]:
        src = api[nid]["inputs"].get("input") or api[nid]["inputs"].get("image")
        api.pop(nid)
        if not src:
            continue
        for n in api.values():
            for k, v in _refs(n):
                if v[0] == nid:
                    n["inputs"][k] = list(src)


def _reachable_from_outputs(api):
    oi = cc.object_info()
    out = [i for i, n in api.items()
           if (oi.get(n["class_type"]) or {}).get("output_node")]
    keep, stack = set(), list(out)
    while stack:
        i = stack.pop()
        if i in keep or i not in api:
            continue
        keep.add(i)
        for _k, v in _refs(api[i]):
            stack.append(v[0])
    return keep


def _drop_refine_stage(api):
    """Prune ComfyUI's LTX-2 2nd-stage upscale/refine: decode the stage-1
    (cropped) latent directly, then drop everything now unreachable."""
    ups = [i for i, n in api.items() if n["class_type"] == "LTXVLatentUpsampler"]
    if not ups:
        return
    vid_src = list(api[ups[0]]["inputs"]["samples"])      # (cropguides, 2) video latent
    cropg = api[vid_src[0]]
    sep1 = cropg["inputs"]["latent"]                       # (stage-1 separate, 0)
    aud_src = [sep1[0], 1]                                 # stage-1 audio latent
    for i, n in api.items():
        if n["class_type"] == "VAEDecode":
            n["inputs"]["samples"] = vid_src
        elif n["class_type"] == "LTXVAudioVAEDecode":
            n["inputs"]["samples"] = aud_src
    keep = _reachable_from_outputs(api)
    for i in [i for i in api if i not in keep]:
        api.pop(i)


def _use_ltxv_scheduler(api, steps):
    """Replace the template's partial/distilled ManualSigmas (tuned for the 2-stage
    pipeline) with a full LTXVScheduler denoise — the stripped single stage needs a
    complete schedule or detailed subjects (faces/bodies) come out as particle mist."""
    samplers = [i for i, n in api.items()
                if n["class_type"] == "SamplerCustomAdvanced"]
    if not samplers:
        return
    req = cc.object_info().get("LTXVScheduler", {}).get("input", {}).get("required", {})
    d = {k: cc._widget_default(v) for k, v in req.items()}
    api["ltx_sched"] = {"class_type": "LTXVScheduler", "inputs": {
        "steps": steps, "max_shift": d.get("max_shift", 2.05),
        "base_shift": d.get("base_shift", 0.95),
        "stretch": d.get("stretch", True), "terminal": d.get("terminal", 0.1)}}
    for i in samplers:
        api[i]["inputs"]["sigmas"] = ["ltx_sched", 0]
    for i in [i for i, n in api.items() if n["class_type"] == "ManualSigmas"]:
        api.pop(i, None)


def _seed_dims(path):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
            text=True).strip().split(",")
        return int(out[0]), int(out[1])
    except Exception:
        return 0, 0


def _set_resolution(api, longer_edge, seed_image):
    """The template hardwires latent size to an EmptyImage (→512x288). Override
    EmptyLTXVLatentVideo.width/height with literals from longer_edge + the seed's
    aspect (multiples of 32) so resolution is actually controllable. Low res is the
    #1 cause of mangled faces/particle-mist people."""
    w, h = _seed_dims(seed_image)
    if w and h:
        if w >= h:
            nw, nh = longer_edge, round(longer_edge * h / w)
        else:
            nh, nw = longer_edge, round(longer_edge * w / h)
    else:
        nw, nh = longer_edge, round(longer_edge * 9 / 16)
    nw = max(64, (nw // 32) * 32)
    nh = max(64, (nh // 32) * 32)
    cc.set_input(api, "EmptyLTXVLatentVideo", "width", nw)
    cc.set_input(api, "EmptyLTXVLatentVideo", "height", nh)
    return nw, nh


def build(seed_image, prompt, longer_edge, length, out_prefix, keep_upscale=False,
          steps=28, gguf=None, seed=None):
    wf = json.load(open(TPL))
    api = cc.ui_to_api(wf)

    def one(ct):
        ids = [nid for nid, n in api.items() if n["class_type"] == ct]
        assert len(ids) == 1, f"{ct}: expected 1, got {ids}"
        return ids[0]

    ckpt, te, avae = (one("CheckpointLoaderSimple"),
                      one("LTXAVTextEncoderLoader"),
                      one("LTXVAudioVAELoader"))

    # new GGUF-route loader nodes
    api["gguf_unet"] = {"class_type": "UnetLoaderGGUF",
                        "inputs": {"unet_name": gguf or _pick_gguf()}}
    api["gguf_clip"] = {"class_type": "DualCLIPLoaderGGUF",
                        "inputs": {"clip_name1": GEMMA_TE,
                                   "clip_name2": TEXT_PROJ, "type": "ltxv"}}
    api["vae_video"] = {"class_type": "VAELoaderKJ",
                        "inputs": {"vae_name": VIDEO_VAE,
                                   "device": "main_device", "weight_dtype": "bf16"}}
    api["vae_audio"] = {"class_type": "VAELoaderKJ",
                        "inputs": {"vae_name": AUDIO_VAE,
                                   "device": "main_device", "weight_dtype": "bf16"}}

    # repoint every consumer: (old_node, old_slot) -> (new_node, 0)
    remap = {
        (ckpt, 0): ("gguf_unet", 0),   # MODEL
        (ckpt, 2): ("vae_video", 0),   # video VAE
        (te, 0): ("gguf_clip", 0),     # CLIP
        (avae, 0): ("vae_audio", 0),   # audio VAE
    }
    rewired = 0
    for n in api.values():
        for inp, v in list(n["inputs"].items()):
            if isinstance(v, list) and len(v) == 2:
                key = (str(v[0]), v[1])
                if key in remap:
                    n["inputs"][inp] = [remap[key][0], remap[key][1]]
                    rewired += 1
    for dead in (ckpt, te, avae):
        api.pop(dead, None)

    if not keep_upscale:
        _drop_refine_stage(api)
        _use_ltxv_scheduler(api, steps)        # full denoise for the single stage
    _bypass_class(api, "ResizeImageMaskNode")  # V3 dynamic-combo node; redundant

    # seed image, prompt, size/length, output name
    # Preserve the seed's path RELATIVE to ComfyUI's input/ (LoadImage accepts subdir-relative names).
    # Basename-only dropped a PRIVATE session's sealed subdir (.lucid-priv-<s>/…) so ComfyUI couldn't
    # find the seed; relpath keeps it for private AND non-private. Out-of-tree seeds (CLI) → basename.
    _inp = os.path.join(cc.COMFY_ROOT, "input")
    _rel = os.path.relpath(seed_image, _inp)
    cc.set_input(api, "LoadImage", "image",
                 _rel if not _rel.startswith("..") else os.path.basename(seed_image))
    pos, _neg = cc.pos_neg_text_nodes(api)
    if pos:
        api[pos]["inputs"]["text"] = prompt
    cc.set_input(api, "ResizeImagesByLongerEdge", "longer_edge", longer_edge)
    _set_resolution(api, longer_edge, seed_image)   # real output-resolution knob
    # Force a literal video length: the flattened template wires length to
    # GetImageSize.width (=512) by mistake, so it over-runs to ~505 frames.
    cc.set_input(api, "EmptyLTXVLatentVideo", "length", length)
    cc.set_input(api, "PrimitiveInt", "value", length)        # audio frames -> match
    cc.set_input(api, "LTXVEmptyLatentAudio", "frames_number", length)
    if seed is not None:
        cc.set_input(api, "RandomNoise", "noise_seed", seed)  # reproducible (lucid passes a seed)
    for sv in ("SaveVideo", "CreateVideo"):
        cc.set_input(api, sv, "filename_prefix", out_prefix)

    # integrity: every link ref [node_id, slot] must target a node in the graph
    ids = set(api.keys())
    bad = [(nid, n["class_type"], k, v) for nid, n in api.items()
           for k, v in n["inputs"].items()
           if isinstance(v, list) and len(v) == 2
           and isinstance(v[0], str) and isinstance(v[1], int)
           and v[0] not in ids]
    return api, rewired, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-image", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--longer-edge", type=int, default=768)
    ap.add_argument("--length", type=int, default=97)
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--out-prefix", default="10eros_i2v")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--keep-upscale", action="store_true",
                    help="keep ComfyUI's 2nd-stage refine (needs spatial upscaler + 2x VRAM)")
    ap.add_argument("--timeout", type=int, default=2400)
    a = ap.parse_args()

    api, rewired, bad = build(a.seed_image, a.prompt, a.longer_edge,
                              a.length, a.out_prefix, keep_upscale=a.keep_upscale,
                              steps=a.steps)
    out = os.path.join(HERE, "workflows", "10eros-i2v.api.json")
    json.dump(api, open(out, "w"), indent=1)
    oi = cc.object_info()
    unknown = sorted({n["class_type"] for n in api.values()
                      if n["class_type"] not in oi})
    print(f"built {len(api)} nodes | rewired {rewired} inputs | wrote {out}")
    print(f"unknown class_types: {unknown or 'NONE'}")
    print(f"dangling refs: {bad or 'NONE'}")
    if a.submit:
        if unknown:
            print("refusing to submit: unknown node types present")
            sys.exit(2)
        print("submitting...")
        files, _ = cc.generate(api, timeout=a.timeout)
        print("OUTPUT:", files)


if __name__ == "__main__":
    main()
