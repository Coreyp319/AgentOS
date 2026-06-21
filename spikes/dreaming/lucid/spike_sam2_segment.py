#!/usr/bin/env python3
"""ADR-0032 §S — the on-box SAM2 click-to-segment spike (RUN ON THE 4090).

This is the one thing ADR-0032 cannot self-verify: kijai's ComfyUI-segment-anything-2 node and the
SAM2 weights are NOT on the box at draft time, so every node-signature / socket / coordinate-format /
VRAM / license claim in the ADR is web research until this runs. The spike **self-discovers** the real
node schema from ComfyUI's /object_info (it does NOT trust the web-research names), executes a minimal
point -> MASK -> SaveImage graph on a test frame at lucid's real resolution, and measures peak VRAM with
nvidia-smi. It then prints a ready-to-paste "ADR-0032 §S CAPTURE" block.

It deliberately reuses the app's OWN ComfyUI client (comfy_client) so the spike drives the GPU exactly
the way lucid does (ADR-0001: one validated client). It is read-only w.r.t. the app — a standalone probe.

USAGE (on the box, with ComfyUI running):
  ./spike_sam2_segment.py discover                 # safe: just dump the SAM2 node + loader schemas
  ./spike_sam2_segment.py run [--image F.png] [--point X,Y] [--width 768 --height 1344]
  ./spike_sam2_segment.py                          # discover, then run with a generated test frame

If the node/weights are missing, `discover` prints the exact install + weight-fetch commands and exits.
Env: COMFY_HOST (default 127.0.0.1:8188), COMFY_ROOT (default /home/corey/ComfyUI).
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time

# the app's validated ComfyUI client lives one dir up (spikes/dreaming/comfy_client.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import comfy_client as cc  # noqa: E402

INPUT_DIR = os.path.join(cc.COMFY_ROOT, "input")
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# Heuristics for self-discovery (names vary across kijai node versions — we MATCH, not assume).
SEG_HINTS = ("sam2",)                       # class name contains this (lowercased)
SEG_NEG = ("video",)                        # but skip the video/stateful variants for a single frame
LOADER_HINTS = ("sam2", "sam_2")            # the model-loader companion
# the +point input — match "coordinates_positive" but NOT the auto-segmenter's "points_per_side"
COORD_NAME_HINTS = ("coordinates", "positive", "coord")


def _oi():
    try:
        return cc.object_info()
    except Exception as e:
        sys.exit(f"FATAL: cannot reach ComfyUI /object_info at {cc.BASE} ({e}).\n"
                 f"Start ComfyUI first, or set COMFY_HOST.")


def _classes_matching(oi, hints, neg=()):
    out = []
    for name in oi:
        low = name.lower()
        if any(h in low for h in hints) and not any(n in low for n in neg):
            out.append(name)
    return sorted(out)


def _schema(oi, cls):
    """A compact (section, name, type, is_widget) view of a class's inputs + its outputs."""
    info = oi.get(cls) or {}
    rows, inp = [], info.get("input", {})
    for section in ("required", "optional"):
        for name, spec in (inp.get(section) or {}).items():
            t = spec[0] if isinstance(spec, list) else spec
            tname = ("COMBO[" + str(len(t)) + "]") if isinstance(t, list) else str(t)
            rows.append((section, name, tname, cc._is_widget(spec)))
    outs = list(zip(info.get("output", []), info.get("output_name", []) or info.get("output", [])))
    return rows, outs


def _print_schema(oi, cls):
    rows, outs = _schema(oi, cls)
    print(f"\n  ── {cls} ──")
    for section, name, tname, is_widget in rows:
        kind = "widget" if is_widget else "LINK  "
        print(f"     {section:<8} {kind} {name:<24} {tname}")
    print(f"     outputs: " + ", ".join(f"{n}:{t}" for t, n in outs))


def _output_slot(oi, cls, want_type):
    for i, t in enumerate((oi.get(cls) or {}).get("output", [])):
        if t == want_type:
            return i
    return None


def _input_named(oi, cls, want_type=None, name_hints=()):
    """First input (required, then optional) matching a type and/or a name hint."""
    inp = (oi.get(cls) or {}).get("input", {})
    for section in ("required", "optional"):
        for name, spec in (inp.get(section) or {}).items():
            t = spec[0] if isinstance(spec, list) else spec
            if want_type is not None and t != want_type:
                continue
            if name_hints and not any(h in name.lower() for h in name_hints):
                continue
            return name
    return None


def discover(oi, quiet=False):
    """Return (seg_cls, loader_cls, model_out_type) or exit with install help."""
    seg = _classes_matching(oi, SEG_HINTS, SEG_NEG)
    seg = [c for c in seg if _input_named(oi, c, want_type="IMAGE")]  # must take an image
    # prefer the POINT-prompt segmenter (has a coordinates input) over an auto/everything segmenter
    seg.sort(key=lambda c: (0 if _input_named(oi, c, name_hints=COORD_NAME_HINTS) else 1, c))
    loaders = _classes_matching(oi, LOADER_HINTS)
    loaders = [c for c in loaders if (oi.get(c) or {}).get("output")]  # must output a model handle
    if not seg or not loaders:
        print("SAM2 segmentation node NOT FOUND in /object_info.\n"
              "Install kijai's node + the small checkpoint on the box, then restart ComfyUI:\n"
              f"  cd {cc.COMFY_ROOT}/custom_nodes\n"
              "  git clone https://github.com/kijai/ComfyUI-segment-anything-2\n"
              "  pip install -r ComfyUI-segment-anything-2/requirements.txt\n"
              f"  mkdir -p {cc.COMFY_ROOT}/models/sam2\n"
              "  # fetch sam2.1_hiera_small (the node's loader can also auto-download); CONFIRM the\n"
              "  # checkpoint LICENSE + sha256 for the SMALL variant for the ADR §S capture.\n"
              "  systemctl --user restart comfyui   # or however ComfyUI is launched\n")
        sys.exit(2)
    if not quiet:
        print("DISCOVERED candidate nodes (ground truth from /object_info):")
        print("  segmentation:", seg)
        print("  loader:      ", loaders)
        for c in seg + loaders:
            _print_schema(oi, c)
    seg_cls, loader_cls = seg[0], loaders[0]
    model_out = ((oi.get(loader_cls) or {}).get("output") or ["SAM2MODEL"])[0]
    return seg_cls, loader_cls, model_out


# ── VRAM measurement (nvidia-smi peak sampler — honest, not the catalog weight; ADR-0004 lesson) ──
class GpuPeak:
    def __init__(self, period=0.2):
        self.period, self.max_mib, self._run = period, 0, False
        self._t = None

    def _sample(self):
        while self._run:
            try:
                out = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    text=True).strip().splitlines()[0]
                self.max_mib = max(self.max_mib, int(out))
            except Exception:
                pass
            time.sleep(self.period)

    def __enter__(self):
        self._run = True
        self._t = threading.Thread(target=self._sample, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *a):
        self._run = False
        if self._t:
            self._t.join(timeout=1)


def _stats_free():
    try:
        d = cc._get("/system_stats")["devices"][0]
        return d.get("vram_free"), d.get("vram_total")
    except Exception:
        return None, None


def make_test_image(path, w, h):
    """A simple high-contrast object-on-ground test frame so single-point segmentation has something
    unambiguous to grab. PIL only here (the spike's one optional dep)."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), (24, 28, 40))
    d = ImageDraw.Draw(im)
    cx, cy, rr = w // 2, h // 2, min(w, h) // 4
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(220, 180, 90))  # the "object"
    im.save(path)
    return (cx, cy)


def build_segment_graph(oi, seg_cls, loader_cls, model_out, image_name, point, out_prefix,
                        model_name="sam2.1_hiera_small.safetensors"):
    """Build the minimal point->MASK->SaveImage api graph generically from the DISCOVERED schema."""
    img_in = _input_named(oi, seg_cls, want_type="IMAGE")
    model_in = _input_named(oi, seg_cls, want_type=model_out) or _input_named(oi, seg_cls, name_hints=("sam2", "model"))
    coord_in = _input_named(oi, seg_cls, want_type="STRING", name_hints=COORD_NAME_HINTS) \
        or _input_named(oi, seg_cls, name_hints=COORD_NAME_HINTS)
    mask_slot = _output_slot(oi, seg_cls, "MASK")
    if not (img_in and model_in and coord_in) or mask_slot is None:
        sys.exit(f"Auto-wire failed: image_in={img_in} model_in={model_in} coord_in={coord_in} "
                 f"mask_slot={mask_slot}. Read the `discover` schema above and wire by hand.")
    px, py = point
    api = {
        "load": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "loader": {"class_type": loader_cls, "inputs": {}},
        "seg": {"class_type": seg_cls, "inputs": {}},
    }
    # fill REQUIRED widget defaults for loader + seg, then override the connections + the point
    for cls, nid in ((loader_cls, "loader"), (seg_cls, "seg")):
        for nm, spec in cc._required_widget_specs(cls):
            dv = cc._widget_default(spec)
            if dv is not None:
                api[nid]["inputs"][nm] = dv
    # the loader's `model` combo defaults to the FIRST option (base_plus); force the requested checkpoint
    for nm, spec in cc._required_widget_specs(loader_cls):
        opts = spec[0] if isinstance(spec, list) and isinstance(spec[0], list) else []
        if any(isinstance(o, str) and o.endswith(".safetensors") for o in opts):
            api["loader"]["inputs"][nm] = model_name
            break
    api["seg"]["inputs"][img_in] = ["load", 0]
    api["seg"]["inputs"][model_in] = ["loader", 0]
    api["seg"]["inputs"][coord_in] = json.dumps([{"x": int(px), "y": int(py)}])
    api["m2i"] = {"class_type": "MaskToImage", "inputs": {"mask": ["seg", mask_slot]}}
    api["save"] = {"class_type": "SaveImage", "inputs": {"images": ["m2i", 0], "filename_prefix": out_prefix}}
    return api, dict(image_in=img_in, model_in=model_in, coord_in=coord_in, mask_slot=mask_slot)


def images_of(history):
    out = []
    for o in history.get("outputs", {}).values():
        for item in o.get("images", []):
            fn = item.get("filename", "")
            if fn and os.path.splitext(fn)[1].lower() in IMG_EXTS:
                out.append(os.path.join(cc.OUTPUT_DIR, item.get("subfolder", ""), fn))
    return out


def run(args):
    oi = _oi()
    seg_cls, loader_cls, model_out = discover(oi, quiet=False)

    # resolve the test image into ComfyUI's input dir + the point (pixels)
    name = "spike_sam2_test.png"
    dst = os.path.join(INPUT_DIR, name)
    os.makedirs(INPUT_DIR, exist_ok=True)
    if args.image:
        from PIL import Image
        Image.open(args.image).convert("RGB").save(dst)
        with Image.open(dst) as im:
            w, h = im.size
        point = tuple(int(v) for v in args.point.split(",")) if args.point else (w // 2, h // 2)
    else:
        point = make_test_image(dst, args.width, args.height)
        if args.point:
            point = tuple(int(v) for v in args.point.split(","))

    model_name = getattr(args, "model", None) or "sam2.1_hiera_small.safetensors"
    api, wiring = build_segment_graph(oi, seg_cls, loader_cls, model_out, name, point, "spike_sam2_mask",
                                      model_name=model_name)
    print("\nGRAPH (auto-wired from discovered schema):")
    print(json.dumps(api, indent=1))

    cc.free_vram()                                   # clear so the delta is the segment's footprint
    time.sleep(1.0)
    base_free, total = _stats_free()
    t0 = time.time()
    with GpuPeak() as peak:
        try:
            base_used = peak.max_mib  # first sample after free
            pid, _ = cc.submit(api)
            hist = cc.wait(pid, timeout=args.timeout)
        except Exception as e:
            sys.exit(f"segment graph FAILED: {e}\n(read the `discover` schema and check the wiring above)")
    dt = time.time() - t0
    after_free, _ = _stats_free()
    masks = images_of(hist)

    resident_mib = (base_free - after_free) // (1024 * 1024) if (base_free and after_free) else None
    print("\n" + "=" * 72)
    print("ADR-0032 §S CAPTURE — paste this block into the ADR Verification gate")
    print("=" * 72)
    print(f"  segmentation node : {seg_cls}")
    print(f"  loader node       : {loader_cls}   (output model type: {model_out})")
    print(f"  image input       : {wiring['image_in']}")
    print(f"  model input       : {wiring['model_in']}")
    print(f"  point input       : {wiring['coord_in']}   (format: JSON '[{{\"x\":int,\"y\":int}}]')")
    print(f"  MASK output slot  : {wiring['mask_slot']}")
    print(f"  measured PEAK VRAM: {peak.max_mib} MiB used (set SEG_PEAK_MIB ~= this + ADR-0004 margin)")
    print(f"  resident delta    : {resident_mib} MiB (free before-after; cross-check)" if resident_mib is not None
          else "  resident delta    : (system_stats unavailable)")
    print(f"  total VRAM        : {(total//(1024*1024)) if total else '?'} MiB")
    print(f"  run time          : {dt:.1f}s   mask file: {masks[0] if masks else 'NONE — investigate'}")
    print(f"  TODO by hand      : checkpoint LICENSE (Apache-2.0?) + sha256 of the SMALL variant; the")
    print(f"                      negative-point input name; whether a keep_model_loaded widget exists.")
    print("=" * 72)
    if not masks:
        sys.exit("No mask image returned — the IO path or wiring is wrong; do NOT trust the VRAM number.")


def main():
    ap = argparse.ArgumentParser(description="ADR-0032 §S on-box SAM2 click-to-segment spike")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("discover")
    rp = sub.add_parser("run")
    for p in (rp, ap):
        p.add_argument("--image", default=None, help="test frame (else a generated one)")
        p.add_argument("--point", default=None, help="X,Y in pixels (else image center)")
        p.add_argument("--width", type=int, default=768)
        p.add_argument("--height", type=int, default=1344)
        p.add_argument("--model", default="sam2.1_hiera_small.safetensors", help="SAM2 checkpoint to measure")
        p.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()
    if args.cmd == "discover":
        discover(_oi(), quiet=False)
    else:
        run(args)


if __name__ == "__main__":
    main()
