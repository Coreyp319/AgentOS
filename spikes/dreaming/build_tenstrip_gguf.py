#!/usr/bin/env python3
"""Make TenStrip's full 10Eros workflow runnable on 24 GB.

TenStrip's shipped graph loads the 34 GB `10Eros_v1-fp8mixed_learned` checkpoint
(won't fit a 4090) and uses the disabled RTX super-res node. This swaps the
checkpoint/TE/audio-VAE loaders for the GGUF route, bypasses the RTX node, fixes
Windows-style lora paths, then emits a tweakable UI workflow into ComfyUI's browser.
Verified by round-tripping the wiring through ui_to_api.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comfy_client as cc          # noqa: E402
import build_10eros_i2v as b       # noqa: E402
import ui_workflow as uw           # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "workflows", "10Eros_10SNodes_I2V_v3_TiledSampler.json")
OUT = os.path.expanduser(
    "~/ComfyUI/user/default/workflows/10Eros_TenStrip_GGUF_I2V.json")


def swap_loaders(api):
    def one(ct):
        ids = [i for i, n in api.items() if n["class_type"] == ct]
        return ids[0] if ids else None
    ckpt, te, avae = (one("CheckpointLoaderSimple"),
                      one("LTXAVTextEncoderLoader"), one("LTXVAudioVAELoader"))
    api["gguf_unet"] = {"class_type": "UnetLoaderGGUF",
                        "inputs": {"unet_name": b._pick_gguf()}}
    api["gguf_clip"] = {"class_type": "DualCLIPLoaderGGUF",
                        "inputs": {"clip_name1": b.GEMMA_TE,
                                   "clip_name2": b.TEXT_PROJ, "type": "ltxv"}}
    api["vae_video"] = {"class_type": "VAELoaderKJ",
                        "inputs": {"vae_name": b.VIDEO_VAE,
                                   "device": "main_device", "weight_dtype": "bf16"}}
    api["vae_audio"] = {"class_type": "VAELoaderKJ",
                        "inputs": {"vae_name": b.AUDIO_VAE,
                                   "device": "main_device", "weight_dtype": "bf16"}}
    remap = {}
    for src, slot, dst in [(ckpt, 0, "gguf_unet"), (ckpt, 1, "gguf_clip"),
                           (ckpt, 2, "vae_video"), (te, 0, "gguf_clip"),
                           (avae, 0, "vae_audio")]:
        if src:
            remap[(src, slot)] = (dst, 0)
    for n in api.values():
        for k, v in list(n["inputs"].items()):
            if isinstance(v, list) and len(v) == 2 and (str(v[0]), v[1]) in remap:
                n["inputs"][k] = list(remap[(str(v[0]), v[1])])
    for d in (ckpt, te, avae):
        if d:
            api.pop(d, None)


def bypass_unknown(api, ct):
    """Remove nodes of class `ct` (e.g. the disabled RTX super-res), passing their
    first linked input through to consumers."""
    for nid in [i for i, n in api.items() if n["class_type"] == ct]:
        src = next((v for v in api[nid]["inputs"].values()
                    if isinstance(v, list) and len(v) == 2 and isinstance(v[1], int)),
                   None)
        api.pop(nid)
        if not src:
            continue
        for n in api.values():
            for k, v in list(n["inputs"].items()):
                if isinstance(v, list) and len(v) == 2 and str(v[0]) == nid:
                    n["inputs"][k] = list(src)


def fix_backslashes(api):
    for n in api.values():
        for k, v in n["inputs"].items():
            if isinstance(v, str) and "\\" in v:
                n["inputs"][k] = v.replace("\\", "/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-image",
                    default=os.path.expanduser("~/ComfyUI/input/10eros_seed.png"))
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--out-prefix", default="tenstrip")
    ap.add_argument("--submit", action="store_true",
                    help="run the swapped graph directly (skip UI emit)")
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()

    api = cc.ui_to_api(json.load(open(SRC)))
    swap_loaders(api)
    bypass_unknown(api, "RTXVideoSuperResolution")
    fix_backslashes(api)
    cc.set_input(api, "LoadImage", "image", os.path.basename(a.seed_image))
    if a.prompt:
        pos, _neg = cc.pos_neg_text_nodes(api)
        if pos:
            api[pos]["inputs"]["text"] = a.prompt
    for sv in ("SaveVideo", "VHS_VideoCombine", "CreateVideo"):
        cc.set_input(api, sv, "filename_prefix", a.out_prefix)
        cc.set_input(api, sv, "filename", a.out_prefix)
    keep = b._reachable_from_outputs(api)
    for i in [i for i in api if i not in keep]:
        api.pop(i, None)

    oi = cc.object_info()
    unknown = sorted({n["class_type"] for n in api.values()
                      if n["class_type"] not in oi})
    ids = set(api.keys())
    dangling = [(i, n["class_type"], k, v) for i, n in api.items()
                for k, v in n["inputs"].items()
                if isinstance(v, list) and len(v) == 2 and isinstance(v[1], int)
                and str(v[0]) not in ids]
    print(f"api nodes={len(api)}  unknown={unknown or 'NONE'}  "
          f"dangling={dangling[:3] or 'NONE'}")
    if unknown or dangling:
        print("NOT emitting (graph not clean)")
        sys.exit(1)

    if a.submit:
        # VHS_VideoCombine doesn't fire via headless /prompt; force a core SaveVideo
        # on the same decoded frames so the heavy video branch actually executes.
        vhs = [i for i, n in api.items() if n["class_type"] == "VHS_VideoCombine"
               and n["inputs"].get("save_output")]
        imgs = api[vhs[0]]["inputs"].get("images") if vhs else None
        if imgs:
            api["force_cv"] = {"class_type": "CreateVideo",
                               "inputs": {"images": list(imgs), "fps": 25}}
            api["force_save"] = {"class_type": "SaveVideo",
                                 "inputs": {"video": ["force_cv", 0],
                                            "filename_prefix": a.out_prefix,
                                            "format": "auto", "codec": "auto"}}
            print("injected core SaveVideo on", imgs)
        print("submitting TenStrip GGUF graph...")
        files, _ = cc.generate(api, timeout=a.timeout)
        print("OUTPUT:", files)
        return

    ui, _ = uw.api_to_ui(api)
    rt = cc.ui_to_api(ui)
    if uw._wiring(api) != uw._wiring(rt):
        print("round-trip wiring MISMATCH — not emitting")
        sys.exit(1)
    print("round-trip wiring: OK")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(ui, open(OUT, "w"), indent=1)
    print(f"wrote {OUT}  ({len(ui['nodes'])} nodes, {len(ui['links'])} links)")


if __name__ == "__main__":
    main()
