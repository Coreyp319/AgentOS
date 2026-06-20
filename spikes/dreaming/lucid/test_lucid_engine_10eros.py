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
