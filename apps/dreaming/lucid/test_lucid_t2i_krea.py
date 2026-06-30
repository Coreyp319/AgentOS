#!/usr/bin/env python3
"""Krea 2 opening backend (ADR-0055): the t2i-engine selector defaults to the known-good
'illustrious' path, flips to 'krea2' via env/registry, and the krea graph rating-gates its
text encoder (stock for sfw, abliterated for mature). Hermetic — no GPU, no ComfyUI.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lucid_t2i as T  # noqa: E402  (self-bootstraps comfy_client / lucid_models paths on import)


def _clean_env():
    os.environ.pop("LUCID_T2I_ENGINE", None)


def test_default_engine_is_illustrious():
    _clean_env()
    assert T._t2i_engine() == "illustrious", "default must stay the known-good SDXL opener"


def test_env_flips_to_krea2():
    os.environ["LUCID_T2I_ENGINE"] = "krea2"
    try:
        assert T._t2i_engine() == "krea2"
    finally:
        _clean_env()


def test_sfw_uses_stock_encoder():
    enc = T._workflow_krea("p", 1, 768, 1344, "sfw")["clip"]["inputs"]["clip_name"]
    assert "abliterated" not in enc.lower() and "huihui" not in enc.lower(), f"sfw leaked the abliterated encoder: {enc}"


def test_mature_uses_abliterated_encoder():
    enc = T._workflow_krea("p", 1, 768, 1344, "mature")["clip"]["inputs"]["clip_name"]
    assert "abliterated" in enc.lower(), f"mature must use the abliterated encoder, got {enc}"


def test_unspecified_rating_defaults_sfw():
    # an omitted rating must NOT engage the uncensored encoder (fail-safe)
    enc = T._workflow_krea("p", 1, 768, 1344)["clip"]["inputs"]["clip_name"]
    assert "abliterated" not in enc.lower()


def test_krea_graph_shape():
    g = T._workflow_krea("p", 7, 768, 1344, "sfw")
    assert g["unet"]["class_type"] == "UNETLoader"
    assert g["unet"]["inputs"]["unet_name"] == "Krea/krea2_turbo_fp8_scaled.safetensors"
    assert g["clip"]["inputs"]["type"] == "krea2"
    assert g["lat"]["class_type"] == "EmptySD3LatentImage"
    assert g["neg"]["class_type"] == "ConditioningZeroOut"
    s = g["smp"]["inputs"]
    assert s["sampler_name"] == "euler" and s["scheduler"] == "beta" and s["cfg"] == T.KREA_CFG
    assert g["sav"]["inputs"]["filename_prefix"] == "lucid-opening"


def test_illustrious_builder_unchanged():
    g = T._workflow("p", 1, 768, 1344)
    assert g["ckpt"]["class_type"] == "CheckpointLoaderSimple", "the known-good SDXL builder must be intact"


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
