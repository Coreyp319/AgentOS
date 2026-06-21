#!/usr/bin/env python3
"""AgentOS Lucid — thin slice of the interactive branching dream loop (ADR-0014).

THROWAWAY SPIKE. It runs generation **directly**, with NO VRAM-coordinator lease
yet (ship gate, ADR-0006/0010/0013). Its only job is to prove that
"click -> the story continues" is worth building.

One turn of the loop:
    parent.out_frame  ---------------------------------+  (i2v first frame)
                                                       v
    story-so-far  --(Ollama, keep_alive:0)-->  beats[{label, prompt}]
                                                       |
    chosen prompt + first frame + seed  --(Remix-i2v)--+--> new clip
                                                            |
    ffmpeg last-frame  <----------------------------------- + (becomes next anchor)

State is a branching tree on disk (clips + tree.json). Each node:
    {id, parent, label, prompt, seed, clip, out_frame, caption}

Reuses spikes/dreaming/comfy_client.py for ComfyUI; reuses the Remix-i2v
workflow we built. Honors ADR-0009/0014 defaults: SFW unless told otherwise, and
a fail-closed red-line (no minors, no real identifiable people) in the LLM
contract. NOT consent-gated / NOT lease-arbitrated — that lands when it leaves
the spike.
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import urllib.request

# import the shared ComfyUI client from the parent spike dir
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import comfy_client as cc  # noqa: E402
import lucid_models  # noqa: E402  (registry: the beat model is an editable affiliation, not hardcoded)
import build_10eros_i2v as ltx  # noqa: E402  (the verified LTX-2.3 / 10Eros i2v graph builder)

HERE = os.path.dirname(os.path.abspath(__file__))
# Non-distilled GGUF i2v (Enhanced nolight, real CFG + 20 steps) + NSFW-22 explicit-anatomy LoRA on the
# LOW-noise expert only (high stays 0.0 — see LORA_HIGH/LORA_LOW): fixes the distilled Remix's melt
# AND the LoRA-on-high "anatomy from within anatomy" regression. ~4x the time per beat. Revert to pure
# non-distilled = enhNSFW-nolight-i2v.api.json; to distilled = LUCID_WORKFLOW=<remix .json>.
# DEFAULT is now the 4+4 Lightning-on-low SPEED lane (lightx2v on the low expert + 8-step 4/4 split):
# ~4.5 min/beat, 3x faster than the 20-step lane, anatomy validated on hands + turn/bend beats
# (2026-06-19). Revert to the 20-step lane: LUCID_WORKFLOW=<…/lucid-nolight-nsfw-i2v.api.json>.
REPO_WF = os.path.join(HERE, "..", "workflows", "lucid-nolight-nsfw-i2v-4x4.api.json")
WORKFLOW = os.environ.get("LUCID_WORKFLOW", os.path.abspath(REPO_WF))
INPUT_DIR = os.path.join(cc.COMFY_ROOT, "input")
DREAMS_DIR = os.environ.get(
    "LUCID_DREAMS", os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "agentos", "dreams"))

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
# beat-gen is a tiny text task — resolve the model from the registry ("narrator" role) so it is an
# editable affiliation, not hardcoded. A SMALL model (qwen2.5vl:3b, 3.2 GB) frees in ~3s under
# force_evict and can coexist with the lighter video models; gemma4 (9.6 GB) was slow/wedge-prone to
# evict and blocked the video step every turn (ADR-0015 §3 force-evict / ADR-0018 small-model lane).
MODEL = os.environ.get("LUCID_MODEL") or lucid_models.get("narrator", "qwen2.5vl:3b")
# Beat-gen ("what happens next" menu) sampling temperature — ABOVE the shared 0.6 fidelity default of
# ground_frame/decompose. The move-taxonomy now supplies divergence STRUCTURALLY, so 0.78 only restores a
# little surprise without the off-frame drift the old 0.9 caused. Beat-gen ONLY — never raise the
# captioning/decompose path (they need fidelity). Tune live with LUCID_BEAT_TEMP; drop toward 0.75 first
# if outputs ever wander off the grounded frame, before touching the prompt.
BEAT_TEMP = float(os.environ.get("LUCID_BEAT_TEMP", "0.78"))
# Beat-gen NARRATOR model — the model that WRITES the "what happens next" menu (NOT frame grounding, which
# always stays on MODEL, the vision model). Defaults to MODEL so beat-gen still SEES the frame and nothing
# changes. Point it at a less-restrictive narrator via LUCID_NARRATOR_MODEL or the registry "narrator-beats"
# role: qwen2.5vl:3b is TIMID in the mature lane and thinner on dream-logic (verified A/B 2026-06-21). The
# goal is a SMALL but not-timid model so eviction stays cheap (ADR-0015 §3 / ADR-0018 — a 14B is slower to
# evict and can delay the video step). When this differs from MODEL the beat-gen call runs TEXT-ONLY (the
# alt model may not be vision-capable) and works from the caption ground_frame already put in the context.
# Shipped ON: the registry sets narrator-beats=hermes3:3b (smaller+faster-evicting than the 3.2GB vision
# model, valid JSON every trial, richer dream-logic, modestly bolder). Clear that role (or set
# LUCID_NARRATOR_MODEL=qwen2.5vl:3b) to fold beat-gen back onto the single vision model. See
# [[lucid-beatgen-prompt-redesign]].
NARRATOR_MODEL = os.environ.get("LUCID_NARRATOR_MODEL") or lucid_models.get("narrator-beats", MODEL)

DEFAULT_W, DEFAULT_H, DEFAULT_LEN = 720, 1280, 33  # ~2s portrait @16fps; matches the
# workflow's baked WanImageToVideo length and stays under the VRAM-thrash line (ADR-0014 §6)
# A user-chosen "next segment length" is bounded HERE (code disposes): at 720x1280 on the non-distilled
# GGUF, beats past 49f (3s) run past the 1800s gen timeout (and toward the VRAM-thrash line); Wan's latent
# stride wants 4k+1 frame counts. 17..49f ≈ 1..3s @16fps. (Raise once a faster expert — fp8 — lands.)
MIN_LEN, MAX_LEN = 17, 49
# I2V ModelSamplingSD3 shift. Shared by BOTH i2v paths, so the default stays 8.0: that is the distilled
# Remix's baked schedule AND a sane 720x1280 value (Wan shift tracks resolution, not distillation —
# higher res wants higher shift). Drop to ~5.0 via LUCID_SHIFT to calm motion on the non-distilled graph;
# do NOT lower it on the Remix path.
DEFAULT_SHIFT = float(os.environ.get("LUCID_SHIFT", "8.0"))
# Explicit-anatomy LoRA (NSFW-22) strength, SPLIT BY EXPERT. The HIGH-noise expert lays out bodies, so an
# explicit-anatomy LoRA there fights the layout and melts anatomy ("anatomy from within anatomy") — the
# #1 distortion cause after lightning. Keep it OFF high (0.0) and ON low (~0.6): the playbook's
# low-noise-only rule. Tune live with LUCID_I2V_LORA_HIGH / LUCID_I2V_LORA_LOW and restart.
LORA_HIGH = float(os.environ.get("LUCID_I2V_LORA_HIGH", "0.0"))
LORA_LOW = float(os.environ.get("LUCID_I2V_LORA_LOW", "0.6"))
# Instareal realism LoRA — "the realism filter", the lever behind the best early Wan 2.2 results.
# Like the anatomy LoRA it rides the LOW-noise expert (skin/detail) and stays OFF high (anti-melt).
# Unlike anatomy it is NOT rating-gated — realism applies to every beat. Tune live with
# LUCID_REALISM_HIGH / LUCID_REALISM_LOW and restart (realism on low is the skin/texture win).
REALISM_HIGH = float(os.environ.get("LUCID_REALISM_HIGH", "0.0"))
REALISM_LOW = float(os.environ.get("LUCID_REALISM_LOW", "0.7"))
# Step-distill (lightx2v / Lightning) LoRA — the 4+4 SPEED lever. Rides the LOW-noise expert ONLY
# (rule #2: lightning on the HIGH expert melts bodies); paired with the 8-step 4/4-split graph it cuts
# a beat ~3x (14min -> ~4.5min) with anatomy intact. Full strength on low, hard 0.0 on high. Inert for
# any graph without a lightx2v LoRA node (the 20-step lane is unaffected).
LIGHTNING_HIGH = float(os.environ.get("LUCID_LIGHTNING_HIGH", "0.0"))
LIGHTNING_LOW = float(os.environ.get("LUCID_LIGHTNING_LOW", "1.0"))

# ── i2v ENGINE selection (ADR-0023 10Eros lane) ──────────────────────────────────────────────
# Two interchangeable i2v backends behind run_beat: "wan" (the default — the non-distilled Wan 2.2
# GGUF + NSFW-22 LoRA graph above) and "10eros"/"ltx" (LTX-2.3 10Eros via the verified
# build_10eros_i2v builder). Editable affiliation: LUCID_ENGINE env, else the registry "i2v-engine"
# role, else "wan" (so nothing changes until flipped). 10Eros is a single merged checkpoint, so the
# Wan high/low LoRA split + ModelSamplingSD3 shift do not apply on its path.
ENGINE = (os.environ.get("LUCID_ENGINE")
          or lucid_models.get("i2v-engine", "wan")).strip().lower()  # import-time default
_ENGINE_OVERRIDE = None   # runtime override set by the web toggle (set_engine)
LTX_LONGER = int(os.environ.get("LUCID_LTX_LONGER", "768"))   # longer-edge px; 1024 if VRAM allows
LTX_STEPS = int(os.environ.get("LUCID_LTX_STEPS", "28"))      # verified hand/detail sweet spot
# Default the lucid lane to Q4_K_M: on a LIVE desktop only ~20 GB is free, and Q6's ~19 GB job + the
# coordinator's ~1.2 GB headroom exceeds that, so Q6 leases get admission-refused mid-use. Q4 (~15 GB
# job) admits reliably and is fast; raise to Q6 via LUCID_LTX_GGUF when the GPU is freer (a fresh login).
LTX_GGUF = os.environ.get("LUCID_LTX_GGUF") or "10Eros_v1-Q4_K_M.gguf"
LTX_MIN_LEN, LTX_MAX_LEN = 17, 121   # LTX ~5 s window; snap to its 8-frame latent stride (8k+1)
# VRAM admission estimate (predict-before-load) = the JOB's own allocation, NOT total-GPU. Q4 ~15 GB
# (≈ Wan), Q6 ~19 GB. Must be ≤ free − coordinator_headroom or the lease is refused; Q6 only admits on
# a light desktop. est_mib() picks per the active quant. (The ~22 GB I first used was total-GPU incl.
# desktop — wrong as an `est`; it refused every lease.)
EST_MIB_WAN, EST_MIB_LTX = 17000, 17000   # LTX default lane = Q4_K_M (~Wan footprint)

# ── LTX-ONLY keyframe-guide conditioning (spatial feed-forward) ───────────────────────────────────
# Maps a viewer-tag intent (more|less|hold|change) to an LTXVAddGuide `strength` — how hard the model
# is pinned to the tagged frame at that video position. "hold" (keep this exact moment) pins hardest;
# "less" (de-emphasize) pins softest. Unknown/missing tags fall to a neutral 0.6 (see _inject_ltx_guides).
LTX_GUIDE_STRENGTH = {"hold": 0.9, "more": 0.7, "change": 0.55, "less": 0.4}

# ── REGIONAL attention steering (ADR-0025 amendment, GPU-verified 2026-06-20) ──────────────────────
# When a note carries a spatial point (x,y,r normalized), the whole guide chain upgrades from
# LTXVAddGuide to LTXVAddGuideAdvancedAttention: a soft-disc MASK localizes the guide's self-attention
# influence to the tapped region (comfy/ldm/lightricks/model.py: weights = pixel_mask * attention_strength,
# pixel_mask=1 ⇒ attend-to-the-guide-HERE). This is the NEW knob — how hard the noisy tokens attend to a
# guide in its region: hold/more attend hard (keep/emphasize), change/less attend weakly (let it move).
# attention_strength==1.0 + no mask is the identity (model no-ops), so a region-LESS guide in an attention
# chain stays neutral — that's how a mixed chain (some notes spatial, some not) keeps the legacy ones
# unchanged while satisfying the model's seed-keyframe accounting (every keyframe carries one entry).
LTX_ATTN_STRENGTH = {"hold": 1.0, "more": 0.85, "change": 0.40, "less": 0.25}
# Required-input defaults for LTXVAddGuideAdvancedAttention (crf/blur are its "more motion" levers; kept
# neutral so enabling regions doesn't change fidelity, only spatial attention). ComfyUI 400s on any miss.
LTX_ATTN_ADV = {"crf": 29, "blur_radius": 0, "interpolation": "lanczos", "crop": "disabled"}
# Kill-switch: LUCID_LTX_ATTENTION=0 forces the legacy plain-guide path even when notes carry regions
# (drops the spatial masks, fail-safe to pre-amendment behaviour). Default on.
LTX_ATTENTION_ENABLED = os.environ.get("LUCID_LTX_ATTENTION", "1") != "0"

# ── ADR-0032: segmentation-produced mask gate (model proposes, code disposes) ──────────────────────
# A SAM2 click-to-segment mask is a NON-deterministic pixel field; load_validated_mask is the deterministic
# gate it must pass before it can become a guide's attention_mask (it replaces the soft-disc as the PRIMARY
# producer; the disc stays the fail-open floor). SHIPPED defaults — tunable, not undefined.
SEG_BINARIZE = 0.5      # cut SAM's 0..1 field (saved 0..255) at this fraction of full-scale -> clean 0/255
SEG_MIN_AREA = 0.003    # reject specks (segmenter caught nothing meaningful under the tap)
SEG_MAX_AREA = 0.85     # reject a near-full-frame mask (an ambiguous tap that grabbed the background)
SEG_ASPECT_TOL = 0.15   # reject-to-disc when the tag-time frame aspect differs from the seed by > this
# Master kill-switch: LUCID_SEGMENT_ENABLED=0 forces the legacy soft-disc path even when a note carries a
# stored segmentation mask (fail-safe to ADR-0025), composing under LUCID_LTX_ATTENTION. Default on.
SEGMENT_ENABLED = os.environ.get("LUCID_SEGMENT_ENABLED", "1") != "0"


def _ltx_softdisc_mask(out_abs, w, h, x, y, r):
    """Write a soft-disc grayscale MASK PNG (white inside the tapped region, linearly feathered to black)
    for LoadImageMask(channel='red'). (x,y,r) normalized 0..1, origin top-left. The model downsamples this
    to the guide latent grid, so exact size is non-critical; we match the anchor for faithful placement.
    Lazy numpy/PIL import keeps the engine's cold path dependency-free when no region is in play."""
    import numpy as np
    from PIL import Image
    cx, cy = x * w, y * h
    r_px = max(2.0, r * min(w, h))
    feather = max(1.0, 0.40 * r_px)
    yy, xx = np.ogrid[:h, :w]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    v = np.clip((r_px + feather - dist) / feather, 0.0, 1.0)        # 1 inside, ramp over `feather`, 0 out
    img = (v * 255.0).astype(np.uint8)
    Image.fromarray(np.stack([img, img, img], axis=-1), "RGB").save(out_abs)


def load_validated_mask(src_abs, out_abs, w, h):
    """ADR-0032 deterministic gate: validate a stored segmentation-mask PNG and, on success, write a clean
    seed-sized 0/255 RGB mask to `out_abs` for LoadImageMask(channel='red'), returning True. On ANY reject
    return False so the caller falls back to the ADR-0025 soft-disc. Pure + total (no exception escapes).

    The mask's OWN pixel dimensions are the tag-time frame size, so the tag-time/render-time divergence is
    a code decision, not a silent stretch: if the mask aspect differs from the seed (w,h) by more than
    SEG_ASPECT_TOL we reject (the disc is render-time + seed-sized and always correct); otherwise we
    LETTERBOX-fit (preserve aspect, pad black) — never raw-stretch. Order: load(red) -> aspect-gate ->
    letterbox-resize -> binarize -> empty-gate -> area-gate (computed on the FINAL seed canvas, so the black
    letterbox padding dilutes the area fraction downward — a near-MAX mask can fall below MIN — and bilinear+
    binarize can shift it slightly). Lazy numpy/PIL import keeps the engine's cold path dep-free."""
    try:
        import numpy as np
        from PIL import Image
        with Image.open(src_abs) as im:
            sw, sh = im.size
            arr = np.asarray(im.convert("RGB"))[:, :, 0]      # red channel (matches the soft-disc/LoadImageMask)
        if sw <= 0 or sh <= 0 or w <= 0 or h <= 0:
            return False
        if abs((sw / sh) - (w / h)) / (w / h) > SEG_ASPECT_TOL:   # aspect gate (no silent warp)
            return False
        scale = min(w / sw, h / sh)                            # letterbox-fit into the seed box
        nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
        m = np.asarray(Image.fromarray(arr).resize((nw, nh), Image.BILINEAR))
        canvas = np.zeros((h, w), dtype=np.uint8)
        oy, ox = (h - nh) // 2, (w - nw) // 2
        canvas[oy:oy + nh, ox:ox + nw] = m
        binm = canvas >= int(SEG_BINARIZE * 255)              # binarize -> clean boolean mask
        if not binm.any():                                    # empty (nothing under the tap)
            return False
        area = float(binm.mean())
        if area < SEG_MIN_AREA or area > SEG_MAX_AREA:        # speck / near-full-frame
            return False
        out = (binm.astype(np.uint8) * 255)
        Image.fromarray(np.stack([out, out, out], axis=-1), "RGB").save(out_abs)
        return True
    except Exception as e:
        print(f"LTX seg-mask validation failed ({e}); falling back to soft-disc (code disposes)")
        return False


# ── ADR-0032 producer: click -> SAM2 object mask, inside the warm lease, headroom-gated, fail-open ──
# Confirmed on the box 2026-06-21 (spike_sam2_segment.py): Sam2Segmentation(coordinates_positive JSON
# [{"x":int,"y":int}]) -> MASK, loader DownloadAndLoadSAM2Model, sam2.1_hiera_small Apache-2.0; measured
# ~1.3-1.5 GB peak / 102 MiB resident (keep_model_loaded=False releases). The graph runs INSIDE the
# already-leased warm ComfyUI under its existing batch token (NO second lease -> no self-preemption); the
# only new arbitration is the pre-flight free-VRAM headroom read below.
SEG_MODEL = os.environ.get("LUCID_SEG_MODEL", "sam2.1_hiera_small.safetensors")
SEG_PEAK_MIB = int(os.environ.get("LUCID_SEG_PEAK_MIB", "2048"))   # measured ~1.3-1.5GB + ADR-0004 margin
SEG_HEADROOM_MIB = int(os.environ.get("LUCID_SEG_HEADROOM_MIB", "1024"))


def _comfy_free_mib():
    """Free VRAM (MiB) from ComfyUI /system_stats devices[0]; None if ComfyUI is unreachable (cold) —
    which the caller treats as "do not segment" (warm-only gate, ADR-0032 §2/§5)."""
    try:
        d = json.load(urllib.request.urlopen(f"{cc.BASE}/system_stats", timeout=2))["devices"][0]
        return int(d.get("vram_free", 0)) // (1024 * 1024)
    except Exception:
        return None


def _segment_graph(frame_name, px, py):
    """The confirmed single-shot Sam2Segmentation point->MASK->SaveImage api graph (ADR-0032 §S, measured).
    `frame_name` is INPUT_DIR-relative (sealed subdir for private); (px,py) are PIXEL coords on that frame."""
    return {
        "load": {"class_type": "LoadImage", "inputs": {"image": frame_name}},
        "loader": {"class_type": "DownloadAndLoadSAM2Model",
                   "inputs": {"model": SEG_MODEL, "segmentor": "single_image",
                              "device": "cuda", "precision": "fp16"}},
        "seg": {"class_type": "Sam2Segmentation",
                "inputs": {"sam2_model": ["loader", 0], "image": ["load", 0], "keep_model_loaded": False,
                           "coordinates_positive": json.dumps([{"x": int(px), "y": int(py)}])}},
        "m2i": {"class_type": "MaskToImage", "inputs": {"mask": ["seg", 0]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["m2i", 0], "filename_prefix": "lucid/segmask"}},
    }


def segment_at(frame_name, x, y, out_abs, timeout=120):
    """ADR-0032 producer: segment the object under a NORMALIZED (x,y) tap on `frame_name` (INPUT_DIR-
    relative), validate the returned mask through load_validated_mask sized to the frame's own pixels, and
    write the clean 0/255 mask to out_abs. Returns out_abs on success else None (caller -> soft-disc/point).
    Warm-only + VRAM-headroom gated; TOTAL fail-open (no exception escapes). The mask is stored at the FRAME
    resolution; the render-time _inject_ltx_guides re-validates + resizes it to the actual seed."""
    if not SEGMENT_ENABLED:
        return None
    free = _comfy_free_mib()                       # cold (None) or contended -> do not segment
    if free is None or free < SEG_PEAK_MIB + SEG_HEADROOM_MIB:
        print(f"seg: skip (free={free} MiB < need {SEG_PEAK_MIB + SEG_HEADROOM_MIB}); fall back to disc/point")
        return None
    try:
        from PIL import Image
        with Image.open(os.path.join(INPUT_DIR, frame_name)) as im:
            fw, fh = im.size                       # SAM wants pixel coords on this frame
        # clamp to the LAST valid pixel index (round(1.0*fw) == fw is one past the edge -> OOB point -> empty
        # mask); an edge tap on a real object must still segment it, not silently degrade to the disc.
        px = min(fw - 1, round(min(1.0, max(0.0, float(x))) * fw))
        py = min(fh - 1, round(min(1.0, max(0.0, float(y))) * fh))
        imgs, _hist = cc.generate_image(_segment_graph(frame_name, px, py), timeout=timeout)
        if not imgs:
            return None
        ok = load_validated_mask(imgs[0], out_abs, fw, fh)
        # PRIVACY (ADR-0016/0032): the raw SaveImage lands in the SHARED ~/ComfyUI/output/lucid/ dir, which
        # neither the private burn nor logout reaches. It is single-use scratch the moment the clean mask is
        # sealed to out_abs, so delete it unconditionally (success OR reject) — no silhouette outlives the
        # session, and non-private raw masks don't accumulate unbounded.
        try:
            os.remove(imgs[0])
        except OSError:
            pass
        return out_abs if ok else None
    except Exception as e:
        print(f"seg: segmentation failed ({e}); fall back to disc/point (fail-open)")
        return None


def _is_ltx(name):
    return (name or "").strip().lower() in ("10eros", "ltx", "ltx2", "ltx-2.3")


def current_engine():
    """Live-resolved i2v engine, canonicalized to 'wan' | '10eros'. Precedence: runtime web override
    > LUCID_ENGINE env > registry/import default. Resolved per-call so a live toggle takes effect on
    the next beat without a restart."""
    name = _ENGINE_OVERRIDE or os.environ.get("LUCID_ENGINE") or ENGINE
    return "10eros" if _is_ltx(name) else "wan"


def set_engine(name):
    """Runtime engine override (web toggle). Accepts 'wan'|'10eros'(+ltx aliases); ignores junk.
    Returns the resolved active engine."""
    global _ENGINE_OVERRIDE
    n = (name or "").strip().lower()
    if _is_ltx(n) or n == "wan":
        _ENGINE_OVERRIDE = n
    return current_engine()


def est_mib():
    """VRAM admission estimate (MiB) for the *active* engine + quant — what lucid_linear leases with.
    Q6/Q8 GGUF need ~19 GB (only admits on a light desktop); Q4 ~17 GB (≈ Wan, admits reliably)."""
    if current_engine() != "10eros":
        return EST_MIB_WAN
    return 19000 if ("Q6" in LTX_GGUF.upper() or "Q8" in LTX_GGUF.upper()) else EST_MIB_LTX


def clamp_length(n):
    """Snap a proposed segment length to a Wan-friendly 4k+1 count inside the VRAM-safe band.
    A bad/oversized/None request can never thrash the GPU — the model proposes, code disposes."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return DEFAULT_LEN
    n = max(MIN_LEN, min(MAX_LEN, n))
    return ((n - 1) // 4) * 4 + 1   # snap down to 4k+1 (latent stride)

# ── beat-gen steering: TWO orthogonal DIALS (the fix for "generic suggestions") ───────────────────
# The old prompt's one "subtle motion, hold the pose" rule conflated SMALL MOTION (an i2v last-frame-
# chaining requirement) with SMALL IDEA (the bug): with no axis of divergence the 3B narrator differed
# the n cards only along which micro-fidget (breath / hair / gaze), so every menu read the same.
# `_SYS_TMPL` now holds those two concerns APART — DIAL 1 keeps motion magnitude small (i2v stays
# coherent), DIAL 2 forces each choice onto a DIFFERENT narrative direction (something enters / light
# turns / a feeling shifts / a dream-logic morph / a camera reveal / gravity loosens) that ADVANCES the
# premise — "subtle means small motion, never a small idea." Tuned for a 3B (qwen2.5vl): no bulleted
# move-list (it fixated on the first bullet and emitted it as the label), divergence carried by one
# inline "for example" sentence, the label rule + "prompt"-before-"label" order pinned to the final
# JSON line (a 3B binds the last instruction hardest — this fixed the "..." placeholder labels and the
# collapse-to-1-beat). Verified A/B vs the old prompt on the live narrator (spikes' ab_branch_prompt.py).
# It is the SAME for every rating — only {rating_clause} swaps, so content-rating never bleeds into the
# motion/divergence guidance. {n} + the JSON contract are filled by build_sys(); doubled braces survive
# .format(). Beat-gen also runs at BEAT_TEMP (above the 0.6 fidelity lane) for a little more surprise.
_SYS_TMPL = (
    "You narrate a SILENT, looping DREAM video. Look at the CURRENT FRAME (attached when available) "
    "plus the premise and the story so far, then propose EXACTLY {n} different 'what happens next' "
    "choices that each continue FROM THIS FRAME.\n"
    "KEEP THE MOTION SMALL so the video stays coherent: the next clip starts on this exact frame, so "
    "hold the subject in the SAME pose, spot, and framing — small gestures, breathing, gaze, fabric, a "
    "slow camera push or pull, a light or color change, or one thing morphing in place. Never have the "
    "subject walk off, turn away, teleport, or reposition; the new thing must arrive THROUGH the frame.\n"
    "BUT MAKE EACH IDEA BIG AND DIFFERENT — 'small motion' must never mean a small idea, and do NOT "
    "return {n} versions of the same fidget. Each choice is a real story turn that advances THIS dream "
    "toward its premise or the open question in the story so far. Pull the {n} choices in genuinely "
    "different directions — for example: something new enters or appears at the frame's edge; the light, "
    "weather, or time-of-day turns and changes the mood; a feeling visibly shifts on the subject (calm "
    "to dread, a dawning wonder, a held breath breaking); an on-screen thing morphs in dream-logic "
    "(water to glass, a shadow detaches, a pattern crawls over fabric); the camera slowly pushes in to "
    "reveal a hidden detail or pulls back to reveal new context; or gravity loosens and things drift in "
    "place. Use a different direction for each card — a choice that could fit ANY dream is wrong.\n"
    "For each choice write IN PLAIN ENGLISH, in this order: a \"prompt\" — a concrete present-tense "
    "MOTION description under 40 words (the camera move + the subject's small motion + the ONE thing "
    "that changes, "
    "drifting continuously from this frame); then a \"label\" — a 2-5 plain-word title naming that idea "
    "(e.g. \"A figure at the door\", \"The room floods gold\"). The label must be real words, never "
    "empty, never \"...\", never a category name.\n"
    "{rating_clause} RED LINE (never violate): no minors, no real or identifiable real people.\n"
    "Return ONLY JSON — EXACTLY {n} choices, each a different direction, each with both a prompt and a "
    'label: {{"beats":[{{"prompt":"...","label":"..."}}]}}.'
)
# The ONLY part the inferred content rating swaps. The RED LINE above is rating-independent and is ALSO
# re-enforced deterministically in code (lucid_safety.red_line_ok) on every beat — this clause only
# tunes creative tone, it can never widen the red line.
_RATING_CLAUSE = {
    "sfw": "Keep every choice strictly SFW — no nudity or sexual content.",
    "mature": ("This dream is adult/mature: you MAY propose sensual or explicit continuations when they "
               "fit what is on screen. (Still absolutely no minors and no real, identifiable people.)"),
}


def build_sys(rating, n):
    """The beat-gen system prompt for an inferred content `rating` ('sfw'|'mature'). Unknown -> SFW."""
    clause = _RATING_CLAUSE.get(rating, _RATING_CLAUSE["sfw"])
    return _SYS_TMPL.format(n=n, rating_clause=clause)


# back-compat: the throwaway lucid_engine CLI (propose_beats) and any external reference still resolve
# E.SYS_SFW; .replace leaves {n} + the JSON {{...}} intact so SYS_SFW.format(n=n) keeps working.
SYS_SFW = _SYS_TMPL.replace("{rating_clause}", _RATING_CLAUSE["sfw"])

# ── frame grounding (ADR-0014 §6): the narrator is a VISION model (qwen2.5vl) — let it SEE the frame.
# One short VLM pass captions + content-rates the current frame; "model proposes, code disposes": the
# rating only ever selects the steering clause above + the render LoRA strength, and ANY uncertainty
# (unreachable model, bad JSON, unknown value) collapses to the SAFE default "sfw". Mirrors the
# image-message shape already proven in lucid_b2 (the seed-likeness VLM).
SYS_GROUND = (
    "You are a careful visual analyst for a private, local, single-user dream-video tool. "
    "Look at the attached image. Reply with ONLY JSON: "
    '{"caption":"<one vivid present-tense sentence describing exactly what is on screen>",'
    '"rating":"sfw"}. '
    'Set "rating":"mature" ONLY if the image itself (or the stated premise) is clearly adult, sensual, '
    'or explicit; otherwise "sfw". This rating adjusts creative tone only — it NEVER permits minors or '
    "real, identifiable people."
)

# ── note decomposition (ADR-0014): the i2v workflow takes only ONE seed image, so when the viewer
# tags several moments of the clip they just watched, those EXTRA frames can't be fed to ComfyUI.
# Instead a VISION model SEES every tagged frame + the viewer's per-frame intent and decomposes them
# into ONE concrete i2v continuation prompt. Same image-message shape as SYS_GROUND; same "model
# proposes, code disposes" stance — neutral here, a downstream red-line gate is the safety authority.
SYS_DECOMPOSE = (
    "You are a film-continuation assistant for a private, local, single-user dream-video tool. "
    "You receive several numbered frames taken from the clip the viewer just watched. Each frame has "
    "an intent tag from the viewer: more = emphasize this element going forward, less = reduce or "
    "remove it, hold = keep this exact framing/composition, change = alter it. You are also given the "
    "viewer's chosen next direction for the upcoming beat. "
    "DECOMPOSE all of this into ONE vivid, concrete image-to-video continuation prompt. Ground every "
    "detail ONLY in what is actually visible in the frames — do not invent elements that are not shown. "
    'Reply with ONLY JSON: {"prompt":"<one continuation prompt>"}.'
)


# ---------------- LLM (beat-gen) ----------------
def _ollama_json(system, user, model=MODEL, images=None, temperature=0.6):
    """One JSON-mode chat turn. `images` (list of base64 PNG/JPEG) attaches to the user message so a
    vision model can ground on the actual frame (lucid_b2 shape); None keeps the text-only path.
    `temperature` defaults to 0.6 — the FIDELITY lane that ground_frame + decompose MUST stay on (a higher
    value hallucinates captions and degrades the i2v continuation prompt). Only the beat-gen menu overrides
    it (to BEAT_TEMP) for a little more narrative surprise; never raise the shared default."""
    user_msg = {"role": "user", "content": user}
    if images:
        user_msg["images"] = images
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, user_msg],
        "stream": False,
        "format": "json",
        "keep_alive": 0,            # evict right after -> frees VRAM for video
        # 0.6 is the fidelity lane (captioning/decompose); beat-gen overrides to BEAT_TEMP. The old fixed
        # 0.9 over-favored divergence and drifted off the grounded frame.
        "options": {"temperature": temperature},
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    # first call pays model-load latency (cold weights + possible VRAM evict)
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["message"]["content"]


def frame_to_b64(path):
    """Base64 of an anchor frame for VLM grounding. None on ANY failure (caller falls back to the
    text-only, SFW-default path). Size-bounded — our frames are small PNGs; refuse anything absurd
    rather than read an arbitrarily large file into memory."""
    if not path:
        return None
    try:
        import base64
        with open(path, "rb") as f:
            data = f.read(16 * 1024 * 1024 + 1)   # cap the read; our frames are well under this
        if not data or len(data) > 16 * 1024 * 1024:
            return None
        return base64.b64encode(data).decode("ascii")
    except Exception:
        return None


def ground_frame(frame_b64, premise=None):
    """One VLM pass over the current frame -> (caption|None, rating). `rating` is code-disposed: ONLY a
    literal "mature" opens up; everything else (including an unreachable/garbled model) -> "sfw" (the
    safe default). The caller red-line-checks the caption before persisting/showing/feeding it back."""
    if not frame_b64:
        return None, "sfw"
    user = ("Premise: " + premise.strip() + "\n" if premise else "") + "Describe and rate this frame."
    try:
        data = json.loads(_ollama_json(SYS_GROUND, user, images=[frame_b64]))
    except Exception:
        return None, "sfw"
    cap = data.get("caption") if isinstance(data, dict) else None
    cap = cap.strip()[:200] if isinstance(cap, str) and cap.strip() else None
    rating = "mature" if isinstance(data, dict) and data.get("rating") == "mature" else "sfw"
    return cap, rating


def decompose_notes(beat_prompt, tagged, premise=None):
    """One VLM pass that SEES the viewer's tagged frames + their per-frame intent and decomposes them
    into a single refined i2v continuation prompt. `tagged` is a list of {"b64","tag","text","t"}
    already ordered by t. Returns the prompt (str, <=300 chars) or None on ANY failure/empty (the
    caller then falls back to the text-only path). Same image-message + fail-safe shape as
    ground_frame; safety is the downstream red-line gate's job, not ours."""
    if not tagged:
        return None
    lines = []
    if premise:
        lines.append("Premise: " + premise.strip())
    lines.append("Chosen next direction: " + (beat_prompt or "").strip())
    lines.append("Tagged frames (in order):")
    for i, t in enumerate(tagged):
        note = (t.get("text") or "").strip()
        lines.append(
            "Frame %d [%s]%s" % (i + 1, t.get("tag", ""), (" — " + note) if note else ""))
    user = "\n".join(lines)
    try:
        data = json.loads(_ollama_json(
            SYS_DECOMPOSE, user, images=[t["b64"] for t in tagged]))
    except Exception:
        return None
    prompt = data.get("prompt") if isinstance(data, dict) else None
    prompt = prompt.strip()[:300] if isinstance(prompt, str) and prompt.strip() else None
    return prompt


def _sanitize(beat):
    """code disposes: cap lengths, drop empties. (Red-line filtering is owed to a
    real guard per ADR-0014 §7; this is the spike's minimal hygiene.)"""
    label = (beat.get("label") or "").strip()[:40]
    prompt = (beat.get("prompt") or "").strip()[:400]
    return {"label": label, "prompt": prompt} if label and prompt else None


def propose_beats(context, n=4):
    raw = _ollama_json(SYS_SFW.format(n=n), context, model=NARRATOR_MODEL, temperature=BEAT_TEMP)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    beats = [b for b in (_sanitize(x) for x in data.get("beats", [])) if b]
    return beats[:n]


def story_context(tree, node_id):
    """The path from root to node, as a short synopsis the LLM can continue."""
    chain, cur = [], node_id
    while cur is not None:
        nd = tree["nodes"][str(cur)]
        if nd.get("label") and nd["label"] != "opening":
            chain.append(nd["label"])
        cur = nd.get("parent")
    chain.reverse()
    cap = tree["nodes"][str(node_id)].get("caption")
    parts = []
    if chain:
        parts.append("Story so far: " + " -> ".join(chain) + ".")
    else:
        parts.append("The dream is just beginning.")
    parts.append("On screen now: " + (cap or "the opening image."))
    return " ".join(parts)


# ---------------- video (one beat) ----------------
def extract_last_frame(mp4_path, name, out_path=None):
    """Grab the true last frame. `out_path` (an absolute, caller-owned destination from
    lucid_store) is written verbatim — the privacy layer owns the path, so the privacy-unaware
    engine never re-derives a private location (privacy-review BLOCKER). Else INPUT_DIR/name."""
    if out_path is None:
        os.makedirs(INPUT_DIR, exist_ok=True)
        out_path = os.path.join(INPUT_DIR, name)
    # -sseof -3 + -update: write every frame to one file; the last one survives.
    subprocess.run(
        ["ffmpeg", "-y", "-sseof", "-3", "-i", mp4_path,
         "-update", "1", "-q:v", "2", out_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return name


def extract_frame_at(mp4_path, t, name, out_path=None):
    """Grab the frame at `t` seconds so the next beat anchors on a user-tagged moment
    (spatial feed-forward), not just the clip's end. Same `out_path`/`name` convention as
    extract_last_frame (caller-owned absolute path verbatim, else INPUT_DIR/name). Returns
    the ref `name` on success, None on any failure so the caller can fall back to the last frame."""
    if out_path is None:
        os.makedirs(INPUT_DIR, exist_ok=True)
        out_path = os.path.join(INPUT_DIR, name)
    t = max(0.0, float(t))
    # -ss before -i: fast keyframe seek; -frames:v 1 + -update: write exactly one frame.
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", mp4_path,
             "-frames:v", "1", "-update", "1", "-q:v", "2", out_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return name
    except Exception as e:
        print(f"extract_frame_at({mp4_path}, t={t}) failed: {e}")
        return None


def _set_widgets(wf, prompt, image_name, seed, w, h, length, output_prefix=None):
    for nd in wf["nodes"]:
        t, title = nd["type"], (nd.get("title") or "")
        wv = nd.get("widgets_values")
        if t == "CLIPTextEncode" and "Positive" in title:
            wv[0] = prompt
        elif t == "LoadImage":
            wv[0] = image_name
        elif t == "Seed (rgthree)":
            wv[0] = seed
        elif t == "WanImageToVideo":
            nd["widgets_values"] = [w, h, length, 1]
        elif t == "ModelSamplingSD3":   # both experts: calmer motion, stay near the seed pose
            wv[0] = DEFAULT_SHIFT
        elif t == "VHS_VideoCombine" and isinstance(wv, dict):
            # %date% tokens only expand in ComfyUI's UI frontend; submitted via the API
            # they're taken literally (a dir named "%date:...%"). Use a clean, anchor-
            # derived prefix so clips land in output/lucid/ traceable to their parent.
            # output_prefix lets the caller redirect (e.g. a private subdir, ADR-0016).
            wv["filename_prefix"] = output_prefix or ("lucid/" + os.path.splitext(image_name)[0])


def _is_api_graph(wf):
    """True for an already-converted /prompt API graph (dict of class_type nodes) vs a UI workflow.
    Lets WORKFLOW be either the UI Remix graph OR a non-distilled API graph (the anti-melt swap)."""
    return isinstance(wf, dict) and "nodes" not in wf and bool(wf) and all(
        isinstance(v, dict) and "class_type" in v for v in wf.values())


def _api_prompt_node(api):
    """The positive CLIPTextEncode id: the node whose positive/negative inputs link DIRECTLY to a
    CLIPTextEncode (Wan routes conditioning through WanImageToVideo, so don't trust slot order)."""
    for n in api.values():
        p, q = n.get("inputs", {}).get("positive"), n.get("inputs", {}).get("negative")
        if isinstance(p, list) and isinstance(q, list) and \
           api.get(p[0], {}).get("class_type") == "CLIPTextEncode" and \
           api.get(q[0], {}).get("class_type") == "CLIPTextEncode":
            return p[0]
    return None


def _set_widgets_api(api, prompt, image_name, seed, w, h, length, output_prefix=None, lora_low=LORA_LOW):
    """Parameterize a non-distilled API-format i2v graph (UnetLoaderGGUF experts) — the same knobs
    _set_widgets sets on the UI Remix graph. Negative stays the graph's baked anatomy-quality text.
    `lora_low` is the LOW-noise explicit-anatomy LoRA strength, set by the inferred content rating
    (sfw -> 0.0 keeps the NSFW LoRA OFF so a SFW beat isn't rendered by an explicit-anatomy graph;
    mature -> the tuned strength)."""
    pos = _api_prompt_node(api)
    if pos:
        api[pos]["inputs"]["text"] = prompt
    for n in api.values():
        ct, ins = n["class_type"], n["inputs"]
        if ct == "LoadImage":
            ins["image"] = image_name
        elif ct == "WanImageToVideo":
            ins["width"], ins["height"], ins["length"] = w, h, length
        elif ct == "ModelSamplingSD3":
            ins["shift"] = DEFAULT_SHIFT
        elif ct == "KSamplerAdvanced":
            ins["noise_seed"] = seed
        elif ct == "LoraLoaderModelOnly":
            name = ins.get("lora_name", "").lower()   # high-noise expert lays out bodies → keep LoRA off it
            is_high = ("-h-" in name) or ("_high" in name) or ("high_noise" in name)
            if "lightx2v" in name or "lightning" in name:  # step-distill LoRA — LOW expert only (rule #2)
                ins["strength_model"] = LIGHTNING_HIGH if is_high else LIGHTNING_LOW
            elif "instareal" in name:                  # realism filter — applies regardless of rating
                ins["strength_model"] = REALISM_HIGH if is_high else REALISM_LOW
            else:                                      # explicit-anatomy (NSFW-22) — rating-gated, off high
                ins["strength_model"] = LORA_HIGH if is_high else lora_low
        elif ct == "VHS_VideoCombine":
            ins["filename_prefix"] = output_prefix or ("lucid/" + os.path.splitext(image_name)[0])


def lora_low_for(rating):
    """Render-side disposal of the inferred rating: only "mature" engages the explicit-anatomy LoRA;
    "sfw" (and any unknown) keeps it OFF so the renderer matches the beats it was given."""
    return LORA_LOW if rating == "mature" else 0.0


def _clamp_length_ltx(n):
    """Snap a proposed segment length to LTX's 8-frame latent stride (8k+1) inside its safe band —
    LTX's analogue of clamp_length; a bad/None request can never thrash the GPU (code disposes)."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = DEFAULT_LEN
    n = max(LTX_MIN_LEN, min(LTX_MAX_LEN, n))
    return ((n - 1) // 8) * 8 + 1


def _ltx_cond_latent_vae_sampler(api):
    """Locate the splice points for LTX keyframe-guide injection in a built 10Eros/LTX api graph.
    Returns (cond_node_id, latent_src, vae_src, sampler_id) or None if any is unidentifiable
    (caller then skips injection — fail-open). LTXVAddGuide takes positive/negative CONDITIONING +
    LATENT + VAE and emits (positive, negative, latent); we chain it between the conditioning source
    (the single LTXVConditioning, which the UI workflow chains AddGuide from) and the sampler.

      cond_node_id : the LTXVConditioning id (outputs positive@0, negative@1)
      latent_src   : [node, slot] currently feeding the sampler's `latent_image`
      vae_src      : [node, slot] the video VAE (LTXVAddGuide encodes the guide image with it)
      sampler_id   : the SamplerCustomAdvanced id whose latent_image we rewire to the last guide
    """
    conds = [i for i, n in api.items() if n.get("class_type") == "LTXVConditioning"]
    samplers = [i for i, n in api.items() if n.get("class_type") == "SamplerCustomAdvanced"]
    if len(conds) != 1 or not samplers:
        return None
    cond_id = conds[0]
    # LTXVAddGuide chains on the plain VIDEO latent it can .clone() — NOT a post-concat audio+video
    # NestedTensor (which threw 'NestedTensor object has no attribute clone' on the real 10Eros graph).
    # That video latent is the LTXVImgToVideoInplace output; splice the guides there and reconnect
    # whatever consumed it (LTXVConcatAVLatent.video_latent in the AV graph, or the sampler in a
    # non-AV graph) to the last guide — leaving the sampler pointing at the concat.
    latent_src, consumer = None, None
    inplace_ids = [i for i, n in api.items()
                   if n.get("class_type", "").startswith("LTXVImgToVideoInplace")]
    if inplace_ids:
        prod = inplace_ids[0]
        latent_src = [prod, 0]
        for nid, n in api.items():
            for k, v in n.get("inputs", {}).items():
                if isinstance(v, list) and len(v) == 2 and v[0] == prod and v[1] == 0:
                    consumer = (nid, k); break
            if consumer:
                break
    if latent_src is None or consumer is None:
        # non-AV / no inplace node: chain on whatever feeds the sampler's latent_image directly
        sid = next((i for i in samplers
                    if isinstance(api[i]["inputs"].get("latent_image"), list)), None)
        if sid is None:
            return None
        latent_src = list(api[sid]["inputs"]["latent_image"])
        consumer = (sid, "latent_image")
    # the video VAE: prefer the one the LTXVImgToVideoInplace seed uses; else any non-audio VAELoader*
    vae_src = None
    inplace = [n for n in api.values()
               if n.get("class_type", "").startswith("LTXVImgToVideoInplace")]
    if inplace and isinstance(inplace[0]["inputs"].get("vae"), list):
        vae_src = list(inplace[0]["inputs"]["vae"])
    if vae_src is None:
        vae_node = next((i for i, n in api.items()
                         if n.get("class_type") in ("VAELoaderKJ", "VAELoader")
                         and "audio" not in i.lower()), None)
        if vae_node is not None:
            vae_src = [vae_node, 0]
    if vae_src is None:
        return None
    return cond_id, latent_src, vae_src, consumer


def _inject_ltx_guides(api, guides, length):
    """LTX-ONLY keyframe-guide conditioning (spatial feed-forward), additive + fail-open.

    Splice one LoadImage + one LTXVAddGuide per guide between the graph's LTXVConditioning and its
    sampler, mirroring the UI workflow's chain:
        LTXVConditioning -> AddGuide -> AddGuide -> ... -> sampler
    Each LTXVAddGuide pins `image` at video position `frame_idx` with `strength`, threading
    (positive, negative, latent) slot-for-slot (0,1,2). The single-anchor `first_frame_name` remains
    the seed; guides are an *extra* spatial pin on top of it.

    `guides` = ordered-by-t list of (frame_abs_path, t_seconds, tag) or (…, tag, region) where region is
    (x,y,r) normalized 0..1 or None (ADR-0025 amendment). If ANY guide carries a region AND attention is
    enabled, the WHOLE chain upgrades to LTXVAddGuideAdvancedAttention (region-bearing guides get a
    soft-disc attention mask; region-less ones stay neutral, attention_strength 1.0). With no regions the
    chain is the legacy plain LTXVAddGuide — byte-identical to before. Mutates `api` IN PLACE only on full
    success; on ANY error logs and leaves `api` untouched (single anchor). `length` bounds frame_idx.
    """
    if not guides:
        return api
    try:
        sp = _ltx_cond_latent_vae_sampler(api)
        if sp is None:
            print("LTX guides: could not identify conditioning/latent/vae/sampler — skipping (fail-open)")
            return api
        cond_id, latent_src, vae_src, consumer = sp
        # output frame rate from the LTXVConditioning node (fallback 24)
        fps = 24
        try:
            fr = api[cond_id]["inputs"].get("frame_rate")
            if isinstance(fr, (int, float)) and fr > 0:
                fps = float(fr)
        except Exception:
            pass
        os.makedirs(INPUT_DIR, exist_ok=True)
        # Co-locate guide frames with the SEED so a PRIVATE session's frames stay in its sealed subdir
        # (.lucid-priv-<s>/, which the private burn wipes) — writing to input/ root would leak them AND
        # break name resolution. Read the seed LoadImage's image (input-relative, maybe subdir'd).
        seed_img = next((api[i]["inputs"]["image"] for i, n in api.items()
                         if n.get("class_type") == "LoadImage"
                         and isinstance(api[i].get("inputs", {}).get("image"), str)), "guide.png")
        seed_dir = os.path.dirname(seed_img)            # "" (input/ root) or ".lucid-priv-<s>" (sealed)
        base = os.path.splitext(os.path.basename(seed_img))[0]
        if seed_dir:
            os.makedirs(os.path.join(INPUT_DIR, seed_dir), exist_ok=True)
        # Regional steering (ADR-0025 amendment): the chain upgrades to attention nodes iff ANY note
        # carries a spatial region AND the kill-switch is on. The model's seed-keyframe accounting requires
        # ALL keyframes in the chain to carry an attention entry (you cannot mix plain + attention guides),
        # so it's all-or-nothing per chain — region-less guides become NEUTRAL attention nodes (no-op).
        def _region(g):
            return g[3] if len(g) > 3 else None
        def _mask(g):                                # ADR-0032: a stored segmentation-mask abs path, or None
            return g[4] if len(g) > 4 else None
        # a note localizes the steer via EITHER a segmentation mask (ADR-0032) or a soft-disc region
        # (ADR-0025); either upgrades the whole chain to attention nodes (all-or-nothing invariant).
        use_attn = LTX_ATTENTION_ENABLED and any(_region(g) or _mask(g) for g in guides)
        mask_wh = None
        if use_attn:                                # the mask is pixel-space; size it to the seed aspect
            try:
                from PIL import Image
                with Image.open(os.path.join(INPUT_DIR, seed_img)) as im:
                    mask_wh = im.size                # (w, h)
            except Exception as e:                   # no seed size → fall back to plain guides (fail-open)
                print(f"LTX guides: seed size unreadable ({e}); attention disabled this beat")
                use_attn = False
        # the conditioning sources for the FIRST guide come from the LTXVConditioning outputs
        pos_src, neg_src, lat_src = [cond_id, 0], [cond_id, 1], list(latent_src)
        new_ids = set()                          # nodes WE add — never rewire these back onto themselves
        masked = 0
        for i, g in enumerate(guides):
            frame_abs_path, t_seconds, tag = g[0], g[1], g[2]
            region = _region(g)
            name = os.path.join(seed_dir, f"{base}_guide{i}.png")   # subdir-relative LoadImage name
            shutil.copy(frame_abs_path, os.path.join(INPUT_DIR, name))
            tagl = (tag or "").strip().lower()
            # "hold" = continue FROM this composition -> pin at the clip START (frame 0); more/less/change
            # pin at their own timeline position as a reference keyframe. (This frame_idx mapping is the
            # knob to validate on a real LTX render via verify_guides.py.)
            frame_idx = 0 if tagl == "hold" else max(0, min(int(length) - 1, round(float(t_seconds) * fps)))
            strength = LTX_GUIDE_STRENGTH.get(tagl, 0.6)
            img_id, guide_id = f"g{i}_img", f"g{i}_guide"
            api[img_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
            if use_attn:
                # Localize this guide's attention via a mask, PREFERRING a validated SEGMENTATION mask
                # (ADR-0032) and falling back to the ADR-0025 soft-disc. A guide that places no mask (no seg
                # mask + no region, OR a seg mask that fails the gate with no region) stays NEUTRAL
                # (attention_strength 1.0, no mask) — exactly its plain form (model no-op).
                seg_mask = _mask(g)
                mask_name = os.path.join(seed_dir, f"{base}_guide{i}_mask.png")
                mask_abs = os.path.join(INPUT_DIR, mask_name)
                wrote = False
                if seg_mask and SEGMENT_ENABLED:                 # model proposes -> code disposes (gate)
                    wrote = load_validated_mask(seg_mask, mask_abs, mask_wh[0], mask_wh[1])
                if (not wrote) and region:                       # fall back to the soft-disc at the tap point
                    x, y, r = region
                    _ltx_softdisc_mask(mask_abs, mask_wh[0], mask_wh[1], x, y, r)
                    wrote = True
                attn = LTX_ATTN_STRENGTH.get(tagl, 0.6) if wrote else 1.0   # localized -> tag knob; else neutral
                inputs = {"positive": list(pos_src), "negative": list(neg_src),
                          "vae": list(vae_src), "latent": list(lat_src),
                          "image": [img_id, 0], "frame_idx": frame_idx, "strength": strength,
                          "attention_strength": attn, **LTX_ATTN_ADV}
                if wrote:
                    mask_id = f"g{i}_mask"
                    api[mask_id] = {"class_type": "LoadImageMask",
                                    "inputs": {"image": mask_name, "channel": "red"}}
                    inputs["attention_mask"] = [mask_id, 0]
                    new_ids.add(mask_id); masked += 1
                api[guide_id] = {"class_type": "LTXVAddGuideAdvancedAttention", "inputs": inputs}
            else:
                api[guide_id] = {"class_type": "LTXVAddGuide", "inputs": {
                    "positive": list(pos_src), "negative": list(neg_src),
                    "vae": list(vae_src), "latent": list(lat_src),
                    "image": [img_id, 0], "frame_idx": frame_idx, "strength": strength}}
            new_ids.add(img_id); new_ids.add(guide_id)
            # the next guide (and finally the sampler) chains from THIS guide's outputs
            pos_src, neg_src, lat_src = [guide_id, 0], [guide_id, 1], [guide_id, 2]
        # reconnect the consumer of the video latent (AV concat's video_latent, or the sampler in a
        # non-AV graph) to the last guide's latent output — the sampler stays on the concat
        cons_id, cons_key = consumer
        api[cons_id]["inputs"][cons_key] = list(lat_src)
        # rewire whatever ELSE consumed the conditioning (CFGGuider/STGGuider*) to the last guide's
        # pos/neg. Skip the guide nodes we just added (the first one legitimately reads [cond,0/1]).
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
        kind = f"LTXVAddGuideAdvancedAttention ({masked} masked)" if use_attn else "LTXVAddGuide"
        print(f"LTX guides: injected {len(guides)} {kind} node(s) (fps={fps})")
        return api
    except Exception as e:
        print(f"LTX guide injection failed ({e}); proceeding with single anchor (fail-open)")
        return api


def _run_beat_ltx(prompt, first_frame_name, seed, length, timeout, output_prefix, guides=None):
    """LTX-2.3 / 10Eros i2v beat: reuse the verified build_10eros_i2v graph (GGUF + Gemma TE +
    LTX-2.3 VAEs, 768px, 28-step LTXVScheduler). The anchor frame lives in ComfyUI's input dir
    (lucid's convention); build() reads its aspect and sets a literal resolution + length. 10Eros is
    a single merged checkpoint, so the Wan LoRA/shift knobs don't apply here (content is prompt-driven;
    the rating still gates the prompt + steers beats upstream).

    `guides` (LTX-only spatial feed-forward) is an optional ordered list of (frame_abs_path, t, tag);
    when present we splice LTXVAddGuide nodes into the built graph (additive, fail-open) so the beat
    also conditions on user-tagged moments. The single-anchor `first_frame_name` stays the seed."""
    seed_path = os.path.join(INPUT_DIR, first_frame_name)
    out_prefix = output_prefix or ("lucid/" + os.path.splitext(first_frame_name)[0])
    clamped = _clamp_length_ltx(length)
    api, _rw, _bad = ltx.build(seed_path, prompt, LTX_LONGER, clamped,
                               out_prefix, steps=LTX_STEPS, seed=seed, gguf=LTX_GGUF)
    if guides:                                  # LTX-only keyframe guides (spatial feed-forward)
        api = _inject_ltx_guides(api, guides, clamped)
    files, _hist = cc.generate(api, timeout=timeout)
    if not files:
        raise RuntimeError("generation produced no video")
    return files[0], seed


def run_beat(prompt, first_frame_name, seed=None,
             w=DEFAULT_W, h=DEFAULT_H, length=DEFAULT_LEN, timeout=1800, output_prefix=None,
             rating="sfw", guides=None):
    """Parameterize the i2v workflow (UI Remix OR non-distilled API graph) and generate one clip.
    `rating` ('sfw'|'mature') sets the LOW-noise LoRA strength so a SFW continuation is NOT rendered
    by the explicit-anatomy graph (default SFW = LoRA off; the shipped path passes the sealed rating).
    ENGINE routes to the LTX-2.3 / 10Eros backend when selected (registry/env), Wan otherwise.

    `guides` (LTX-only) = ordered list of (frame_abs_path, t_seconds, tag in more|less|hold|change)
    that pin tagged moments into the clip via LTXVAddGuide (spatial feed-forward). Ignored on the Wan
    path; with guides=None/empty the behavior is byte-identical to before (additive, fail-open)."""
    if seed is None:
        seed = random.randint(1, 2**31)
    if current_engine() == "10eros":
        # pass `guides` only when present so the no-guide call is byte-identical to before
        if guides:
            return _run_beat_ltx(prompt, first_frame_name, seed, length, timeout, output_prefix,
                                 guides=guides)
        return _run_beat_ltx(prompt, first_frame_name, seed, length, timeout, output_prefix)
    length = clamp_length(length)   # defensive: never trust a caller-supplied frame count
    with open(WORKFLOW) as f:
        wf = json.load(f)
    if _is_api_graph(wf):                          # non-distilled GGUF graph (anti-melt) — drive directly
        api = dict(wf)
        _set_widgets_api(api, prompt, first_frame_name, seed, w, h, length, output_prefix,
                         lora_low=lora_low_for(rating))
    else:                                          # UI Remix graph — widgets then convert
        _set_widgets(wf, prompt, first_frame_name, seed, w, h, length, output_prefix)
        api = cc.ui_to_api(wf)
    files, _hist = cc.generate(api, timeout=timeout)
    if not files:
        raise RuntimeError("generation produced no video")
    return files[0], seed


# ---------------- tree state ----------------
def _session_dir(session):
    return os.path.join(DREAMS_DIR, session)


def load_tree(session):
    with open(os.path.join(_session_dir(session), "tree.json")) as f:
        return json.load(f)


def save_tree(session, tree):
    d = _session_dir(session)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "tree.json"), "w") as f:
        json.dump(tree, f, indent=2)


def start(session, opening_image=None, from_clip=None):
    """Create a session whose root 'out_frame' is the opening anchor."""
    os.makedirs(_session_dir(session), exist_ok=True)
    if from_clip:
        out_frame = extract_last_frame(from_clip, f"{session}_n0.png")
        clip = from_clip
    elif opening_image:
        out_frame = f"{session}_n0.png"
        shutil.copy(opening_image, os.path.join(INPUT_DIR, out_frame))
        clip = None
    else:
        raise ValueError("need --image or --from")
    tree = {"session": session, "counter": 0, "current": 0, "nodes": {
        "0": {"id": 0, "parent": None, "label": "opening", "prompt": None,
              "seed": None, "clip": clip, "out_frame": out_frame, "caption": None}}}
    save_tree(session, tree)
    return tree


def step(session, prompt, label, seed=None, parent=None):
    """Generate one clip continuing from `parent` (default: current)."""
    tree = load_tree(session)
    parent = tree["current"] if parent is None else parent
    anchor = tree["nodes"][str(parent)]["out_frame"]
    clip, used_seed = run_beat(prompt, anchor, seed=seed)
    nid = tree["counter"] + 1
    out_frame = extract_last_frame(clip, f"{session}_n{nid}.png")
    tree["nodes"][str(nid)] = {
        "id": nid, "parent": parent, "label": label, "prompt": prompt,
        "seed": used_seed, "clip": clip, "out_frame": out_frame, "caption": None}
    tree["counter"] = nid
    tree["current"] = nid
    save_tree(session, tree)
    return tree["nodes"][str(nid)]


# ---------------- CLI (headless test harness) ----------------
def _main():
    ap = argparse.ArgumentParser(description="Lucid dream-loop spike (ADR-0014)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("session")
    s.add_argument("--image"); s.add_argument("--from", dest="from_clip")
    b = sub.add_parser("beats"); b.add_argument("session")
    st = sub.add_parser("step"); st.add_argument("session")
    st.add_argument("--choose", type=int, help="index into proposed beats")
    st.add_argument("--prompt", help="type-your-own beat (overrides --choose)")
    st.add_argument("--label", default="custom")
    args = ap.parse_args()

    if args.cmd == "start":
        t = start(args.session, args.image, args.from_clip)
        print(f"started '{args.session}' root out_frame={t['nodes']['0']['out_frame']}")
    elif args.cmd == "beats":
        t = load_tree(args.session)
        ctx = story_context(t, t["current"])
        print("context:", ctx)
        for i, bt in enumerate(propose_beats(ctx)):
            print(f"  [{i}] {bt['label']}  ::  {bt['prompt']}")
    elif args.cmd == "step":
        t = load_tree(args.session)
        if args.prompt:
            prompt, label = args.prompt, args.label
        else:
            beats = propose_beats(story_context(t, t["current"]))
            chosen = beats[args.choose or 0]
            prompt, label = chosen["prompt"], chosen["label"]
        print(f"generating: {label} :: {prompt}")
        nd = step(args.session, prompt, label)
        print(f"-> node {nd['id']} clip={nd['clip']}")


if __name__ == "__main__":
    _main()
