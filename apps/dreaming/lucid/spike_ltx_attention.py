#!/usr/bin/env python3
"""SPIKE (ADR-0025 follow-on) — wire LTXVAddGuideAdvancedAttention into the 10Eros/LTX guide chain so a
viewer's tagged moment becomes a SPATIAL, regional attention guide ("more/hold HERE, let it change THERE")
instead of today's whole-frame keyframe pin (plain LTXVAddGuide).

WHY THIS NODE (all verified on this box, 2026-06-20):
  * It is already installed — ComfyUI-LTXVideo @4f45fd6 (2026-06-17); registered in __init__.py:71.
  * Its per-region attention mask is actually CONSUMED at sample time by ComfyUI *core*:
    comfy/ldm/lightricks/model.py:_build_guide_self_attention_mask reads each guide's
    {strength(=attention_strength), pixel_mask, latent_shape} (propagated via model_base.py:1126) and
    builds a log-space additive self-attention bias:  weights = pixel_mask * attention_strength.
    -> pixel_mask=1 means ATTEND to the guide here (apply it); 0 means IGNORE it here (region free).
       A white soft-disc inside the tapped region is therefore the correct polarity.
  * It drops into the SAME LTXVConditioning -> sampler seam the production _inject_ltx_guides splices,
    so this is a node-class swap + two extra knobs, not a new pipeline.

WHAT THIS SPIKE PROVES / SURFACES:
  1. The injection is structurally valid (dry-run dumps the graph; ComfyUI /prompt accepts it).
  2. The seed-keyframe accounting invariant (model.py:1104, total_pfc == kf_grid_mask) HOLDS once
     attention entries exist — this guard is DORMANT for plain LTXVAddGuide and only activates here, so
     it can only be closed by a real GPU run. --run / --ab do that and report it precisely.
  3. The mask reaches the model and changes the tagged region (A/B at one seed).

This file is ADDITIVE + FAIL-OPEN and does NOT modify committed engine code. It reuses the real engine's
anchor-finder (E._ltx_cond_latent_vae_sampler), builder (E.ltx.build) and ComfyUI client (E.cc), so the
graph proven here is the graph the app would run. Productionizing = fold inject_attention_guides() into
lucid_engine._inject_ltx_guides (guarded by an env/flag) and add optional (x,y,r) to the note schema
(lucid_linear.add_note + the /api/note route + the React tap handler).

Guide spec (extends verify_guides.py's PATH@T[:TAG]):   PATH@T[:TAG][#X,Y,R]
  T        seconds into the parent clip the moment was tagged
  TAG      more|less|hold|change            (default hold)
  #X,Y,R   OPTIONAL normalized region: center (X,Y) + radius R, each in [0,1], origin top-left.
           Omit the #... and the guide is a GLOBAL per-guide attention_strength (no spatial mask).

Modes:
  --dry-run [--api-graph G]   build+inject+dump+assert. No GPU. With --api-graph it injects into a
                              pre-converted graph (e.g. workflows/10eros-i2v.api.json) so it needs no
                              ComfyUI at all; otherwise it calls E.ltx.build (needs ComfyUI /object_info).
  --run                       submit ONE guided beat via ComfyUI (real GPU). Forces the 10Eros engine.
  --ab                        render TWICE at one seed: (A) attention guide, NO mask; (B) same guide with
                              a tight region mask + reduced attention_strength. The masked region should
                              visibly move while the rest stays — the proof the spatial mask bites.

Examples:
  ./spike_ltx_attention.py --anchor f.png --prompt "she turns to the sea" \\
      --guide f.png@1.2:more#0.30,0.40,0.18 --dry-run
  ./spike_ltx_attention.py --anchor f.png --prompt "..." --api-graph ../workflows/10eros-i2v.api.json --dry-run
  LUCID_ENGINE=10eros ./spike_ltx_attention.py --anchor f.png --prompt "..." \\
      --guide f.png@1.2:change#0.7,0.5,0.2 --run
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_engine as E   # the real engine: build (ltx), anchor-finder, run/generate, current_engine  # noqa: E402

TAGS = ("more", "less", "hold", "change")

# ── coordinator lease (production-faithful VRAM gate) ────────────────────────────────────────────
# ComfyUI runs here as a standalone systemd service, so the right primitive is COOPERATIVE Acquire
# (caller owns its process), NOT Spawn (agentosd launches+owns — would collide on :8188). Acquire does
# predict-before-load admission + serialization exactly as the real Lucid loop's lease_spawn does, minus
# the spawn. Heavy lane (batch) is fail-CLOSED: if est doesn't fit MEASURED free VRAM it's DENIED (the
# daemon's only auto-reclaim before the gate is `ollama stop`; it does NOT evict nimbus-flux/UE yet —
# that graphics-yield is the known off-lock integration). A denial here is the correct, honest verdict.
COORD = ("org.agentos.Coordinator1", "/org/agentos/Coordinator1", "org.agentos.Coordinator1")


def _coord(method, *sig_and_args):
    cmd = ["busctl", "--user", "call", *COORD, method, *map(str, sig_and_args)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def lease_status():
    """(free_mib:int|None) from the live coordinator Status (b s t u → ...,free_mib)."""
    r = _coord("Status")
    if r.returncode != 0:
        return None
    parts = r.stdout.split()
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return None


def lease_acquire(est, tier="batch"):
    """Cooperative Acquire(tier, est). Returns (token|None, outcome_msg). None token = denied/unreachable
    → the spike fails open (no forced VRAM), exactly like the real loop."""
    r = _coord("Acquire", "su", tier, int(est))
    if r.returncode != 0:
        return None, f"coordinator unreachable: {r.stderr.strip() or r.stdout.strip()}"
    parts = r.stdout.split(maxsplit=3)           # "bts  true 7 \"granted …\""
    granted = len(parts) >= 2 and parts[1] == "true"
    token = parts[2] if len(parts) >= 3 else "0"
    msg = parts[3].strip().strip('"') if len(parts) >= 4 else ""
    return (token if granted and token != "0" else None), msg


def lease_release(token):
    if token and token != "0":
        _coord("Release", "t", token)


def submit_leased(api, label, est, use_lease):
    """Submit one graph to ComfyUI, optionally gated by a cooperative lease. Returns the video path.
    Raises RuntimeError with the lease verdict if admission denies (the production-faithful outcome)."""
    token = None
    if use_lease:
        free = lease_status()
        print(f"[{label}] lease: requesting batch est={est}M (coordinator reports free≈{free}M)…")
        token, msg = lease_acquire(est)
        if token is None:
            raise RuntimeError(
                f"lease DENIED — {msg or 'admission refused'}. This is the correct fail-closed verdict: "
                f"est {est}M does not fit measured free VRAM, and the daemon cannot evict the live "
                f"wallpaper/UE yet (graphics-yield is the off-lock gap). Free that VRAM, then re-run.")
        print(f"[{label}] lease GRANTED (token {token}) — serialized + admitted.")
    try:
        files, _hist = E.cc.generate(api, timeout=1800)
    finally:
        lease_release(token)
        if token:
            print(f"[{label}] lease released (token {token}).")
    if not files:
        raise RuntimeError("generation produced no video")
    return files[0]

# The NEW knob this node unlocks: per-guide ATTENTION strength (how hard the noisy tokens attend to this
# guide). Parallel to E.LTX_GUIDE_STRENGTH (the latent keyframe pin) but semantically distinct:
#   hold/more -> attend HARD to the guide in its region (keep / emphasize it)
#   change/less -> attend WEAKLY (let the region re-imagine / recede)
# These are the curve to tune on the GPU, exactly as frame_idx was for plain guides. attention_strength
# == 1.0 AND no mask is the identity (model.py:1166 returns no-op) -> safe fallback to plain-guide behavior.
LTX_ATTN_STRENGTH = {"hold": 1.0, "more": 0.85, "change": 0.40, "less": 0.25}

# Full required-input defaults for LTXVAddGuideAdvancedAttention (guide.py INPUT_TYPES). ComfyUI /prompt
# 400s on any missing required widget, so we spell them all out. crf/blur are the Advanced "more motion"
# levers; we keep them neutral (same image fidelity as plain LTXVAddGuide) so A/B isolates the MASK.
ADV_DEFAULTS = {"crf": 29, "blur_radius": 0, "interpolation": "lanczos", "crop": "disabled"}


def parse_guide(spec):
    """`PATH@T[:TAG][#X,Y,R]` -> (abs_path, t_seconds, tag, region|None) where region=(x,y,r) in [0,1]."""
    region = None
    if "#" in spec:
        spec, reg = spec.rsplit("#", 1)
        try:
            x, y, r = (float(v) for v in reg.split(","))
        except ValueError:
            sys.exit(f"bad region '#{reg}': expected #X,Y,R (three floats in 0..1)")
        for nm, v in (("X", x), ("Y", y), ("R", r)):
            if not (0.0 <= v <= 1.0):
                sys.exit(f"region {nm}={v} out of range [0,1]")
        region = (x, y, r)
    if "@" not in spec:
        sys.exit(f"bad --guide '{spec}': expected PATH@T[:TAG][#X,Y,R]")
    path, rest = spec.rsplit("@", 1)
    tag = "hold"
    if ":" in rest:
        rest, tag = rest.split(":", 1)
    if tag not in TAGS:
        sys.exit(f"bad tag '{tag}': expected one of {TAGS}")
    try:
        t = float(rest)
    except ValueError:
        sys.exit(f"bad time '{rest}' in --guide (seconds, float)")
    ap = os.path.abspath(path)
    if not os.path.exists(ap):
        sys.exit(f"guide frame not found: {ap}")
    return ap, t, tag, region


def softdisc_mask(out_abs, w, h, x, y, r):
    """Write a soft-disc grayscale MASK PNG (white inside the tapped region, feathered to black outside).
    (x,y,r) normalized 0..1, origin top-left. Saved RGB grayscale so LoadImageMask(channel='red') reads it.
    Returns (w, h). The model downsamples this to the guide latent grid (model.py:_downsample_mask_to_latent),
    so exact size is non-critical; we match the anchor for a faithful preview."""
    cx, cy = x * w, y * h
    r_px = max(2.0, r * min(w, h))
    feather = max(1.0, 0.40 * r_px)                      # linear edge so the region blends, no hard ring
    yy, xx = np.ogrid[:h, :w]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    v = np.clip((r_px + feather - dist) / feather, 0.0, 1.0)   # 1 inside, ramp over `feather`, 0 outside
    img = (v * 255.0).astype(np.uint8)
    Image.fromarray(np.stack([img, img, img], axis=-1), "RGB").save(out_abs)
    return w, h


def _seed_dir_base(api):
    """Mirror _inject_ltx_guides: co-locate our PNGs with the seed LoadImage so a PRIVATE session's
    frames stay inside its sealed subdir. Returns (seed_dir, base_stem)."""
    seed_img = next((api[i]["inputs"]["image"] for i, n in api.items()
                     if n.get("class_type") == "LoadImage"
                     and isinstance(api[i].get("inputs", {}).get("image"), str)), "guide.png")
    return os.path.dirname(seed_img), os.path.splitext(os.path.basename(seed_img))[0]


def inject_attention_guides(api, guides, length, anchor_size, force_no_mask=False):
    """ADDITIVE + FAIL-OPEN: splice one LTXVAddGuideAdvancedAttention (+ optional LoadImageMask) per guide
    between the graph's LTXVConditioning and its sampler — the same surgery as _inject_ltx_guides, but
    emitting the attention node and a spatial mask. Mutates `api` IN PLACE only on full success.

    `guides`      = ordered-by-t list of (abs_path, t, tag, region|None).
    `anchor_size` = (w, h) of the anchor, for mask resolution.
    Returns (api, manifest) where manifest is a per-guide list of what was wired (for asserts/printing).
    On ANY error: logs, returns (api_untouched, []) — caller falls back to plain guides / single anchor."""
    manifest = []
    if not guides:
        return api, manifest
    try:
        sp = E._ltx_cond_latent_vae_sampler(api)
        if sp is None:
            print("attn-guides: could not identify conditioning/latent/vae/sampler — skipping (fail-open)")
            return api, manifest
        cond_id, latent_src, vae_src, consumer = sp
        fps = 24
        fr = api[cond_id]["inputs"].get("frame_rate")
        if isinstance(fr, (int, float)) and fr > 0:
            fps = float(fr)
        os.makedirs(E.INPUT_DIR, exist_ok=True)
        seed_dir, base = _seed_dir_base(api)
        if seed_dir:
            os.makedirs(os.path.join(E.INPUT_DIR, seed_dir), exist_ok=True)
        w, h = anchor_size
        pos_src, neg_src, lat_src = [cond_id, 0], [cond_id, 1], list(latent_src)
        new_ids = set()
        for i, g in enumerate(guides):
            frame_abs_path, t_seconds, tag, region = g
            tagl = (tag or "").strip().lower()
            name = os.path.join(seed_dir, f"{base}_attn{i}.png")
            shutil.copy(frame_abs_path, os.path.join(E.INPUT_DIR, name))
            frame_idx = 0 if tagl == "hold" else max(0, min(int(length) - 1, round(float(t_seconds) * fps)))
            strength = E.LTX_GUIDE_STRENGTH.get(tagl, 0.6)          # latent keyframe pin (unchanged)
            attn = LTX_ATTN_STRENGTH.get(tagl, 0.6)                 # NEW: per-guide attention strength
            img_id, guide_id = f"a{i}_img", f"a{i}_guide"
            api[img_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
            inputs = {"positive": list(pos_src), "negative": list(neg_src),
                      "vae": list(vae_src), "latent": list(lat_src),
                      "image": [img_id, 0], "frame_idx": frame_idx, "strength": strength,
                      "attention_strength": attn, **ADV_DEFAULTS}
            mask_name = None
            if region is not None and not force_no_mask:
                mask_id = f"a{i}_mask"
                mask_name = os.path.join(seed_dir, f"{base}_attn{i}_mask.png")
                softdisc_mask(os.path.join(E.INPUT_DIR, mask_name), w, h, *region)
                api[mask_id] = {"class_type": "LoadImageMask",
                                "inputs": {"image": mask_name, "channel": "red"}}
                inputs["attention_mask"] = [mask_id, 0]
                new_ids.add(mask_id)
            api[guide_id] = {"class_type": "LTXVAddGuideAdvancedAttention", "inputs": inputs}
            new_ids.add(img_id); new_ids.add(guide_id)
            pos_src, neg_src, lat_src = [guide_id, 0], [guide_id, 1], [guide_id, 2]
            manifest.append({"i": i, "tag": tagl, "frame_idx": frame_idx, "strength": strength,
                             "attention_strength": attn, "region": region, "mask": mask_name,
                             "guide_id": guide_id})
        # reconnect the video-latent consumer (AV concat's video_latent, or the sampler) to the last guide
        cons_id, cons_key = consumer
        api[cons_id]["inputs"][cons_key] = list(lat_src)
        # rewire everything ELSE that consumed the raw conditioning (CFGGuider/STGGuider*) to the last guide
        for nid, n in api.items():
            if nid in new_ids:
                continue
            ins = n.get("inputs", {})
            if isinstance(ins.get("positive"), list) and ins["positive"][:1] == [cond_id] \
               and ins["positive"][1] == 0:
                ins["positive"] = list(pos_src)
            if isinstance(ins.get("negative"), list) and ins["negative"][:1] == [cond_id] \
               and ins["negative"][1] == 1:
                ins["negative"] = list(neg_src)
        masked = sum(1 for m in manifest if m["mask"])
        print(f"attn-guides: injected {len(guides)} LTXVAddGuideAdvancedAttention node(s) "
              f"({masked} with a spatial mask, fps={fps})")
        return api, manifest
    except Exception as e:
        print(f"attn-guide injection failed ({e}); fail-open (no attention guides)")
        return api, []


def _base_api(anchor, prompt, length, seed, api_graph=None):
    """Build (or load) the 10Eros/LTX api graph for an anchor+prompt, exactly like the engine does."""
    clamped = E._clamp_length_ltx(length)
    if api_graph:
        with open(api_graph) as f:
            api = json.load(f)
    else:
        # build() calls ComfyUI /object_info to convert the UI workflow -> api graph (needs ComfyUI up)
        api, _rw, _bad = E.ltx.build(os.path.abspath(anchor), prompt, E.LTX_LONGER, clamped,
                                     "lucid/spike_attn", steps=E.LTX_STEPS, seed=seed, gguf=E.LTX_GGUF)
    return api, clamped


def _assert_wiring(api, manifest):
    """Structural proof for --dry-run: the chain is well-formed and every masked guide reaches a real PNG."""
    n_attn = sum(1 for n in api.values() if n.get("class_type") == "LTXVAddGuideAdvancedAttention")
    assert n_attn == len(manifest), f"expected {len(manifest)} attention nodes, found {n_attn}"
    for m in manifest:
        g = api[m["guide_id"]]["inputs"]
        assert "attention_strength" in g, f"{m['guide_id']} missing attention_strength"
        assert g["attention_strength"] == m["attention_strength"]
        for req in ("crf", "blur_radius", "interpolation", "crop"):
            assert req in g, f"{m['guide_id']} missing required input {req}"
        if m["mask"]:
            ref = g.get("attention_mask")
            assert isinstance(ref, list) and len(ref) == 2, f"{m['guide_id']} mask not wired"
            mnode = api[ref[0]]
            assert mnode["class_type"] == "LoadImageMask", "mask source is not LoadImageMask"
            mpath = os.path.join(E.INPUT_DIR, mnode["inputs"]["image"])
            assert os.path.exists(mpath), f"mask PNG missing on disk: {mpath}"
    # chain integrity: at least one downstream guider/sampler now reads the LAST guide's pos/neg/latent
    last = manifest[-1]["guide_id"]
    downstream = any(
        isinstance(v, list) and v[:1] == [last]
        for nid, n in api.items() if nid not in {m["guide_id"] for m in manifest}
        for v in n.get("inputs", {}).values())
    assert downstream, "no downstream consumer was rewired to the last attention guide"
    print(f"   ✓ {n_attn} attention node(s), chain threaded, "
          f"{sum(1 for m in manifest if m['mask'])} mask PNG(s) on disk, downstream rewired")


def dry_run(anchor, prompt, length, guides, out, api_graph):
    api, clamped = _base_api(anchor, prompt, length, 1234, api_graph)
    os.makedirs(E.INPUT_DIR, exist_ok=True)
    w, h = Image.open(anchor).size if anchor and os.path.exists(anchor) else (768, 1280)
    api, manifest = inject_attention_guides(api, guides, clamped, (w, h))
    with open(out, "w") as f:
        json.dump(api, f, indent=2)
    print(f"\nwrote graph -> {out}  (anchor {w}x{h}, clamped length {clamped})")
    for m in manifest:
        reg = "global" if not m["region"] else f"region(x={m['region'][0]},y={m['region'][1]},r={m['region'][2]})"
        print(f"   {m['tag']:6} frame_idx={m['frame_idx']:>3}  strength={m['strength']}  "
              f"attn={m['attention_strength']}  {reg}")
    if not manifest:
        print("   (no attention guides injected — fail-open path; check the log above)")
        return
    _assert_wiring(api, manifest)
    print("\nGPU-UNVERIFIED: load the JSON into ComfyUI to eyeball the chain, or use --run / --ab for a real render.")


def _force_10eros():
    if E.current_engine() != "10eros":
        try:
            E.set_engine("10eros")
        except Exception:
            pass
    if E.current_engine() != "10eros":
        sys.exit("could not select the 10Eros engine — set LUCID_ENGINE=10eros")


def run(anchor, prompt, length, guides, use_lease=True):
    _force_10eros()
    est = E.est_mib()
    os.makedirs(E.INPUT_DIR, exist_ok=True)
    w, h = Image.open(anchor).size
    api, clamped = _base_api(anchor, prompt, length, 4242, None)
    api, manifest = inject_attention_guides(api, guides, clamped, (w, h))
    if not manifest:
        sys.exit("no attention guides injected — nothing to prove (fail-open). Check the log.")
    print(f"submitting ONE attention-guided beat (engine=10eros, est={est}M, {len(manifest)} guide(s), "
          f"{sum(1 for m in manifest if m['mask'])} masked)…")
    try:
        out = submit_leased(api, "run", est, use_lease)
    except RuntimeError as e:
        # Both a lease DENIAL and the seed-keyframe invariant (model.py:1104) surface here as findings.
        sys.exit(f"\nGPU run did not complete: {e}\n"
                 "(If the message mentions 'pre_filter_counts != keyframe grid mask length', the SEED "
                 "keyframe needs an attention entry too — fold the seed into the attention chain.)")
    print(f"\n✓ GPU-VERIFIED: attention guides rendered -> {out}")
    print("  seed-keyframe accounting (model.py:1104) HELD — the model accepted the attention entries.")


def ab(anchor, prompt, length, guides, use_lease=True):
    """Render twice at one seed to isolate the MASK: (A) attention guide, no mask; (B) + tight region mask."""
    if any(g[3] is None for g in guides):
        sys.exit("--ab needs every --guide to carry a #X,Y,R region (the thing being A/B'd)")
    _force_10eros()
    est = E.est_mib()
    os.makedirs(E.INPUT_DIR, exist_ok=True)
    w, h = Image.open(anchor).size
    SEED = 4242
    outs = {}
    for label, force_no_mask in (("A_nomask", True), ("B_masked", False)):
        api, clamped = _base_api(anchor, prompt, length, SEED, None)
        api, manifest = inject_attention_guides(api, guides, clamped, (w, h), force_no_mask=force_no_mask)
        if not manifest:
            sys.exit(f"{label}: no guides injected (fail-open) — cannot A/B")
        # distinct output prefix per arm so the files don't collide
        for nid, n in api.items():
            if n.get("class_type", "").startswith("SaveAnimated") or "Save" in n.get("class_type", ""):
                if "filename_prefix" in n.get("inputs", {}):
                    n["inputs"]["filename_prefix"] = f"lucid/spike_attn_{label}"
        try:
            outs[label] = submit_leased(api, label, est, use_lease)
        except RuntimeError as e:
            sys.exit(f"[{label}] GPU run did not complete: {e}")
        print(f"[{label}] -> {outs[label]}")
    print("\n✓ A/B COMPLETE — same seed, mask is the only difference:")
    print(f"   A (no mask): {outs['A_nomask']}")
    print(f"   B (masked):  {outs['B_masked']}")
    print("   Compare: the tagged region should move/recede in B (per its tag) while the rest matches A.")


def main():
    ap = argparse.ArgumentParser(description="Spike: LTXVAddGuideAdvancedAttention regional steering for Lucid")
    ap.add_argument("--anchor", help="seed / first frame (as a beat's anchor)")
    ap.add_argument("--prompt", default="the dream drifts onward", help="beat prompt")
    ap.add_argument("--length", type=int, default=49, help="requested LTX frame count (clamped to 8k+1)")
    ap.add_argument("--guide", action="append", default=[], dest="guides",
                    help="PATH@T[:TAG][#X,Y,R]  (repeatable)")
    ap.add_argument("--api-graph", help="(dry-run, OFFLINE) inject into this pre-converted api graph "
                    "instead of building one — no ComfyUI needed")
    ap.add_argument("--out", default="/tmp/lucid_attn_graph.json", help="dry-run graph dump path")
    ap.add_argument("--no-lease", action="store_true",
                    help="(run/ab) bypass the coordinator lease and submit directly — only when VRAM is "
                         "already free; default routes through the production Acquire/Release gate")
    m = ap.add_mutually_exclusive_group(required=True)
    m.add_argument("--dry-run", action="store_true", help="build+inject+dump+assert; no GPU")
    m.add_argument("--run", action="store_true", help="submit one attention-guided beat (real GPU)")
    m.add_argument("--ab", action="store_true", help="render no-mask vs masked at one seed (the proof)")
    a = ap.parse_args()

    guides = [parse_guide(s) for s in a.guides]
    if not a.api_graph and not a.anchor:
        sys.exit("--anchor is required (or pass --api-graph for an offline dry-run injection check)")
    if (a.run or a.ab) and not a.anchor:
        sys.exit("--run/--ab need --anchor")
    if a.dry_run and not guides:
        print("note: no --guide given; this only builds the base graph (nothing to steer)")

    if a.dry_run:
        dry_run(a.anchor, a.prompt, a.length, guides, a.out, a.api_graph)
    elif a.run:
        run(a.anchor, a.prompt, a.length, guides, use_lease=not a.no_lease)
    else:
        ab(a.anchor, a.prompt, a.length, guides, use_lease=not a.no_lease)


if __name__ == "__main__":
    main()
