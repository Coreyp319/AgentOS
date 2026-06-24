#!/usr/bin/env python3
"""run_beat's i2v ENGINE switch (ADR-0023 10Eros lane).

Verifies routing (run_beat -> LTX builder when selected, Wan otherwise), the LTX
length clamp (8k+1 stride), and — when a live ComfyUI + seed image are present —
that the LTX path actually builds an LTX-2.3 graph with the seed applied.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import lucid_engine as E   # noqa: E402
import comfy_client as cc  # noqa: E402


def test_ltx_length_clamp():
    assert E._clamp_length_ltx(33) == 33
    assert E._clamp_length_ltx(49) == 49
    assert E._clamp_length_ltx(40) == 33        # snap down to 8k+1
    assert E._clamp_length_ltx(9999) == 121     # max band = 8*15+1
    assert E._clamp_length_ltx(None) == ((E.DEFAULT_LEN - 1) // 8) * 8 + 1


def test_routes_to_ltx_when_selected():
    calls = {}

    def fake_ltx(prompt, frame, seed, length, timeout, out):
        calls["ltx"] = (prompt, frame, seed, length)
        return ("/x.mp4", seed)
    orig_eng, orig_fn = E.ENGINE, E._run_beat_ltx
    E.ENGINE, E._run_beat_ltx = "10eros", fake_ltx
    try:
        clip, seed = E.run_beat("a prompt", "anchor.png", seed=7, length=33)
    finally:
        E.ENGINE, E._run_beat_ltx = orig_eng, orig_fn
    assert clip == "/x.mp4" and calls.get("ltx") and calls["ltx"][0] == "a prompt"


def test_default_routes_to_wan():
    """Default engine must NOT touch the LTX builder (preserves prior behavior)."""
    touched = {"ltx": False}
    orig_eng, orig_fn, orig_gen = E.ENGINE, E._run_beat_ltx, cc.generate

    def boom(*a, **k):
        touched["ltx"] = True
        return ("/no.mp4", 0)
    E.ENGINE, E._run_beat_ltx = "wan", boom
    cc.generate = lambda api, timeout=1800: (["/wan.mp4"], {})
    try:
        clip, _ = E.run_beat("p", "anchor.png", seed=1, length=33)
    finally:
        E.ENGINE, E._run_beat_ltx, cc.generate = orig_eng, orig_fn, orig_gen
    assert clip == "/wan.mp4" and touched["ltx"] is False


def test_set_engine_and_est():
    orig = E._ENGINE_OVERRIDE
    try:
        assert E.set_engine("10eros") == "10eros"
        assert E.current_engine() == "10eros" and E.est_mib() == E.EST_MIB_LTX
        assert E.set_engine("ltx") == "10eros"          # alias canonicalizes
        assert E.set_engine("wan") == "wan"
        assert E.current_engine() == "wan" and E.est_mib() == E.EST_MIB_WAN
        E.set_engine("garbage")                          # junk ignored -> unchanged
        assert E.current_engine() == "wan"
    finally:
        E._ENGINE_OVERRIDE = orig


def test_ltx_builder_makes_ltx_graph():
    try:
        cc.object_info()
    except Exception:
        print("  (skip: no ComfyUI)")
        return
    seedp = os.path.join(E.INPUT_DIR, "10eros_seed.png")
    if not os.path.exists(seedp):
        print("  (skip: no seed image)")
        return
    cap = {}
    orig = cc.generate
    cc.generate = lambda api, timeout=1800: (cap.setdefault("api", api), (["/f.mp4"], {}))[1]
    try:
        E._run_beat_ltx("woman smiles", "10eros_seed.png", 7, 33, 60, "lucid/test")
    finally:
        cc.generate = orig
    api = cap["api"]
    cts = {n["class_type"] for n in api.values()}
    assert "UnetLoaderGGUF" in cts and "WanImageToVideo" not in cts, cts
    assert any(n["class_type"] == "RandomNoise" and n["inputs"].get("noise_seed") == 7
               for n in api.values()), "seed not applied"


# ── ADR-0025 amendment: _inject_ltx_guides branch (plain vs regional attention), offline ─────────────
import json as _json          # noqa: E402
import shutil as _shutil       # noqa: E402
import tempfile as _tempfile   # noqa: E402
from contextlib import contextmanager  # noqa: E402

_CACHED_GRAPH = os.path.join(os.path.dirname(HERE), "workflows", "10eros-i2v.api.json")


@contextmanager
def _guided_graph_env():
    """Load the cached 10Eros graph + a temp ComfyUI input dir with the seed + guide PNGs the injector
    copies/reads, so _inject_ltx_guides runs fully offline. Restores E.INPUT_DIR after."""
    from PIL import Image
    api = _json.load(open(_CACHED_GRAPH))
    tmp = _tempfile.mkdtemp(prefix="lucid_attn_test_")
    Image.new("RGB", (96, 160), (40, 40, 40)).save(os.path.join(tmp, "flesh_seed.png"))  # the graph's seed
    guide = os.path.join(tmp, "guide.png")
    Image.new("RGB", (96, 160), (90, 90, 90)).save(guide)
    orig = E.INPUT_DIR
    E.INPUT_DIR = tmp
    try:
        yield api, guide
    finally:
        E.INPUT_DIR = orig
        _shutil.rmtree(tmp, ignore_errors=True)


def test_inject_guides_plain_when_no_region():
    # No note carries a region -> legacy plain LTXVAddGuide chain, byte-identical behaviour (no masks).
    with _guided_graph_env() as (api, guide):
        out = E._inject_ltx_guides(api, [(guide, 0.5, "more", None)], 49)
        cts = [n["class_type"] for n in out.values()]
        assert cts.count("LTXVAddGuide") == 1, cts
        assert "LTXVAddGuideAdvancedAttention" not in cts, cts
        assert "LoadImageMask" not in cts, cts


def test_inject_guides_attention_when_region():
    # A region-bearing note upgrades the WHOLE chain to attention nodes; the region one gets a mask +
    # tag-driven attention_strength, the region-less sibling stays neutral (1.0, no mask).
    with _guided_graph_env() as (api, guide):
        guides = [(guide, 0.5, "more", (0.3, 0.4, 0.2)), (guide, 1.0, "change", None)]
        out = E._inject_ltx_guides(api, guides, 49)
        attn = [n for n in out.values() if n["class_type"] == "LTXVAddGuideAdvancedAttention"]
        assert len(attn) == 2, [n["class_type"] for n in out.values()]
        assert "LTXVAddGuide" not in {n["class_type"] for n in out.values()}, "must not mix plain+attn"
        masks = [n for n in out.values() if n["class_type"] == "LoadImageMask"]
        assert len(masks) == 1, "exactly one region -> one mask"
        masked = [n for n in attn if "attention_mask" in n["inputs"]]
        neutral = [n for n in attn if "attention_mask" not in n["inputs"]]
        assert len(masked) == 1 and len(neutral) == 1
        assert masked[0]["inputs"]["attention_strength"] == E.LTX_ATTN_STRENGTH["more"]   # 0.85
        assert neutral[0]["inputs"]["attention_strength"] == 1.0                           # region-less no-op
        # the generated mask PNG really exists on disk where LoadImageMask points
        assert os.path.exists(os.path.join(E.INPUT_DIR, masks[0]["inputs"]["image"]))


def test_inject_guides_killswitch_forces_plain(monkeypatch=None):
    # LUCID_LTX_ATTENTION=0 forces the legacy path even with a region present (fail-safe).
    with _guided_graph_env() as (api, guide):
        orig = E.LTX_ATTENTION_ENABLED
        E.LTX_ATTENTION_ENABLED = False
        try:
            out = E._inject_ltx_guides(api, [(guide, 0.5, "more", (0.3, 0.4, 0.2))], 49)
        finally:
            E.LTX_ATTENTION_ENABLED = orig
        cts = {n["class_type"] for n in out.values()}
        assert "LTXVAddGuide" in cts and "LTXVAddGuideAdvancedAttention" not in cts, cts


# ── ADR-0032: load_validated_mask deterministic gate (model proposes, code disposes) ──────────────
def _seg_mask_png(path, w, h, area_frac=0.2, fill=255):
    """A test segmentation mask: a centered filled rectangle covering ~area_frac of a w×h frame."""
    import math
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), (0, 0, 0))
    if area_frac > 0:
        rw, rh = int(w * math.sqrt(area_frac)), int(h * math.sqrt(area_frac))
        cx, cy = w // 2, h // 2
        ImageDraw.Draw(im).rectangle([cx - rw // 2, cy - rh // 2, cx + rw // 2, cy + rh // 2],
                                     fill=(fill, fill, fill))
    im.save(path)
    return path


def test_seg_mask_accepts_clean_and_writes_seed_sized():
    # a clean mid-area mask passes; the gate writes a SEED-sized, binarized 0/255 PNG for LoadImageMask
    import numpy as np
    from PIL import Image
    d = _tempfile.mkdtemp(prefix="lucid_seg_")
    try:
        src = _seg_mask_png(os.path.join(d, "m.png"), 96, 160, area_frac=0.2)
        out = os.path.join(d, "out.png")
        assert E.load_validated_mask(src, out, 96, 160) is True
        with Image.open(out) as im:
            assert im.size == (96, 160), im.size
            vals = set(np.unique(np.asarray(im.convert("RGB"))[:, :, 0]).tolist())
        assert vals <= {0, 255}, f"must be binarized 0/255, got {vals}"
    finally:
        _shutil.rmtree(d, ignore_errors=True)


def test_seg_mask_rejects_empty_speck_and_near_full():
    # empty (nothing under the tap), a speck (< SEG_MIN_AREA), and near-full-frame (> SEG_MAX_AREA) all reject
    d = _tempfile.mkdtemp(prefix="lucid_seg_")
    try:
        for frac in (0.0, 0.001, 0.95):
            src = _seg_mask_png(os.path.join(d, f"m{frac}.png"), 96, 160, area_frac=frac)
            assert E.load_validated_mask(src, os.path.join(d, "o.png"), 96, 160) is False, frac
    finally:
        _shutil.rmtree(d, ignore_errors=True)


def test_seg_mask_rejects_aspect_mismatch():
    # a landscape mask (160×96) cannot map onto a portrait seed (96×160) -> reject-to-disc, never stretch
    d = _tempfile.mkdtemp(prefix="lucid_seg_")
    try:
        src = _seg_mask_png(os.path.join(d, "m.png"), 160, 96, area_frac=0.2)
        assert E.load_validated_mask(src, os.path.join(d, "o.png"), 96, 160) is False
    finally:
        _shutil.rmtree(d, ignore_errors=True)


def test_seg_mask_binarize_drops_subthreshold_gray():
    # a uniform gray BELOW SEG_BINARIZE*255 (=127) becomes empty after binarize -> reject (no trusting floats)
    d = _tempfile.mkdtemp(prefix="lucid_seg_")
    try:
        src = _seg_mask_png(os.path.join(d, "m.png"), 96, 160, area_frac=0.3, fill=100)
        assert E.load_validated_mask(src, os.path.join(d, "o.png"), 96, 160) is False
    finally:
        _shutil.rmtree(d, ignore_errors=True)


def test_seg_mask_letterbox_resizes_same_aspect_to_seed():
    # a same-aspect mask at a different pixel size letterbox-fits onto the seed grid (output is seed-sized)
    from PIL import Image
    d = _tempfile.mkdtemp(prefix="lucid_seg_")
    try:
        src = _seg_mask_png(os.path.join(d, "m.png"), 48, 80, area_frac=0.2)   # 48:80 == 96:160
        out = os.path.join(d, "out.png")
        assert E.load_validated_mask(src, out, 96, 160) is True
        with Image.open(out) as im:
            assert im.size == (96, 160), im.size
    finally:
        _shutil.rmtree(d, ignore_errors=True)


def test_seg_mask_unreadable_returns_false():
    # a missing/garbage source path is total fail-open (no exception escapes) -> caller uses the disc
    assert E.load_validated_mask("/nonexistent/nope.png", "/tmp/o.png", 96, 160) is False


# ── ADR-0032: the _inject_ltx_guides consumer branch (stored seg mask -> guide, disc as fallback) ──
def test_inject_guides_segmentation_mask_preferred():
    # a guide carrying a stored VALID seg mask -> attention node wired to that BINARIZED mask (sharp 0/255,
    # NOT the feathered disc) with the tag-driven attention_strength.
    import numpy as np
    from PIL import Image
    with _guided_graph_env() as (api, guide):
        d = _tempfile.mkdtemp(prefix="lucid_segwire_")
        try:
            sm = _seg_mask_png(os.path.join(d, "sm.png"), 96, 160, area_frac=0.2)   # matches the seed aspect
            out = E._inject_ltx_guides(api, [(guide, 0.5, "more", None, sm)], 49)
            attn = [n for n in out.values() if n["class_type"] == "LTXVAddGuideAdvancedAttention"]
            masks = [n for n in out.values() if n["class_type"] == "LoadImageMask"]
            assert len(attn) == 1 and len(masks) == 1, [n["class_type"] for n in out.values()]
            assert "attention_mask" in attn[0]["inputs"]
            assert attn[0]["inputs"]["attention_strength"] == E.LTX_ATTN_STRENGTH["more"]
            wired = os.path.join(E.INPUT_DIR, masks[0]["inputs"]["image"])
            with Image.open(wired) as im:
                vals = set(np.unique(np.asarray(im.convert("RGB"))[:, :, 0]).tolist())
            assert vals <= {0, 255}, f"seg mask is binarized, not the feathered disc: {sorted(vals)[:6]}"
        finally:
            _shutil.rmtree(d, ignore_errors=True)


def test_inject_guides_bad_segmask_falls_back_to_disc():
    # an aspect-MISMATCHED seg mask is rejected by the gate; with a region present the soft-disc is used
    # (a mask is still wired -> not neutral), and the disc is FEATHERED (intermediate values, not pure 0/255).
    import numpy as np
    from PIL import Image
    with _guided_graph_env() as (api, guide):
        d = _tempfile.mkdtemp(prefix="lucid_segfb_")
        try:
            bad = _seg_mask_png(os.path.join(d, "bad.png"), 160, 96, area_frac=0.2)   # landscape -> rejected
            out = E._inject_ltx_guides(api, [(guide, 0.5, "more", (0.3, 0.4, 0.2), bad)], 49)
            masks = [n for n in out.values() if n["class_type"] == "LoadImageMask"]
            assert len(masks) == 1, "disc fallback still wires a mask"
            wired = os.path.join(E.INPUT_DIR, masks[0]["inputs"]["image"])
            with Image.open(wired) as im:
                vals = np.unique(np.asarray(im.convert("RGB"))[:, :, 0])
            assert len(vals) > 2, f"the soft-disc is feathered, not pure 0/255: {vals[:6]}"
        finally:
            _shutil.rmtree(d, ignore_errors=True)


def test_inject_guides_segment_killswitch_drops_mask():
    # LUCID_SEGMENT_ENABLED=0: a mask-only note (no region) -> NEUTRAL attention node (no mask, attn 1.0)
    with _guided_graph_env() as (api, guide):
        d = _tempfile.mkdtemp(prefix="lucid_segks_")
        orig = E.SEGMENT_ENABLED
        E.SEGMENT_ENABLED = False
        try:
            sm = _seg_mask_png(os.path.join(d, "sm.png"), 96, 160, area_frac=0.2)
            out = E._inject_ltx_guides(api, [(guide, 0.5, "more", None, sm)], 49)
            attn = [n for n in out.values() if n["class_type"] == "LTXVAddGuideAdvancedAttention"]
            assert len(attn) == 1, "mask presence still upgrades the chain to attention"
            assert "attention_mask" not in attn[0]["inputs"], "killswitch drops the seg mask"
            assert attn[0]["inputs"]["attention_strength"] == 1.0, "neutral when no mask is wired"
        finally:
            E.SEGMENT_ENABLED = orig
            _shutil.rmtree(d, ignore_errors=True)


def _run():
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    print(f"\n{'OK' if not fails else str(fails) + ' FAILED'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    _run()
