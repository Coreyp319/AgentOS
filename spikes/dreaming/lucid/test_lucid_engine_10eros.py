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
