#!/usr/bin/env python3
"""Unit tests for the brief contract — pure Python, no Blender, no GPU.

Run:  python3 tests/test_validate_brief.py    (from spikes/creative-env/)
   or python3 -m pytest tests/                 (if pytest is available)

These are the gate tests for "model proposes / code disposes": prove that an
off-vocabulary brief is REJECTED, and that the palette clamp is deterministic.
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import validate_brief as vb  # noqa: E402

AMBER = os.path.join(ROOT, "briefs", "amber_field.json")


def _load():
    with open(AMBER) as f:
        return json.load(f)


def test_canonical_brief_valid():
    norm = vb.load_and_validate(AMBER)
    assert norm["theme"].startswith("a waving amber field")
    assert norm["_resolved"]["subject"] == "horizon"
    assert len(norm["_resolved"]["palette_rgb"]) == 5


def test_canonical_brief_declares_schema_version():
    norm = vb.load_and_validate(AMBER)
    assert norm["schema"] == vb.SUPPORTED_SCHEMA_VERSION
    assert norm["_resolved"]["schema_version"] == vb.SUPPORTED_SCHEMA_VERSION


def test_reject_missing_schema_version():
    b = _load()
    del b["schema"]
    try:
        vb.validate(b)
    except vb.BriefError as e:
        assert "schema" in str(e)
        return
    raise AssertionError("a brief with no schema version was NOT rejected")


def test_reject_incompatible_schema_major():
    b = _load()
    b["schema"] = "9.0.0"  # a MAJOR this disposer does not understand
    try:
        vb.validate(b)
    except vb.BriefError as e:
        assert "9.0.0" in str(e) and "MAJOR" in str(e)
        return
    raise AssertionError("an incompatible-MAJOR brief was NOT rejected")


def test_reject_non_semver_schema():
    b = _load()
    b["schema"] = "v1"
    try:
        vb.validate(b)
    except vb.BriefError as e:
        assert "SemVer" in str(e) or "schema" in str(e)
        return
    raise AssertionError("a non-SemVer schema string was NOT rejected")


def test_accept_forward_minor_within_major():
    # MINOR ahead of us is additive-forward-compatible within the same MAJOR
    b = _load()
    b["schema"] = "0.9.99"
    norm = vb.validate(b)  # must NOT raise
    assert norm["schema"] == "0.9.99"


def test_reject_offvocab_camera_move():
    b = _load()
    b["camera"]["move"] = "barrel-roll"
    try:
        vb.validate(b)
    except vb.BriefError as e:
        assert "barrel-roll" in str(e)
        return
    raise AssertionError("off-vocab camera.move was NOT rejected")


def test_reject_offvocab_path_render_as():
    b = _load()
    b["path"]["render_as"] = "lava-river"
    try:
        vb.validate(b)
    except vb.BriefError as e:
        assert "lava-river" in str(e)
        return
    raise AssertionError("off-vocab path.render_as was NOT rejected")


def test_reject_subject_not_an_element():
    b = _load()
    b["camera"]["subject"] = "nonexistent"
    try:
        vb.validate(b)
    except vb.BriefError as e:
        assert "subject" in str(e)
        return
    raise AssertionError("dangling camera.subject was NOT rejected")


def test_reject_bad_hex():
    b = _load()
    b["palette"][0] = "#zzzzzz"
    try:
        vb.validate(b)
    except vb.BriefError as e:
        assert "hex" in str(e)
        return
    raise AssertionError("bad hex palette colour was NOT rejected")


def test_reject_unknown_top_level_key():
    b = _load()
    b["hack"] = {"do": "arbitrary"}
    try:
        vb.validate(b)
    except vb.BriefError as e:
        assert "hack" in str(e)
        return
    raise AssertionError("unknown top-level key was NOT rejected")


def test_reject_offvocab_binding_value():
    b = _load()
    b["bindings"]["wind.direction"] = "keylogger.stream"
    try:
        vb.validate(b)
    except vb.BriefError as e:
        assert "keylogger" in str(e)
        return
    raise AssertionError("off-vocab binding value was NOT rejected")


def test_palette_clamp_nearest_deterministic():
    palette = ["#b8862f", "#e3c46a", "#f4e3a1", "#7d5e22", "#9bb04a"]
    # pure red is nearest to the brightest amber-yellow by sRGB euclidean? No —
    # check it lands on SOME palette entry and is stable across calls.
    red = (1.0, 0.0, 0.0)
    a = vb.clamp_color(red, palette)
    b = vb.clamp_color(red, palette)
    assert a == b, "clamp must be deterministic"
    assert vb.rgb_to_hex(a) in [c.lower() for c in palette], "clamp must land IN palette"


def test_palette_clamp_exact_passthrough():
    palette = ["#b8862f", "#e3c46a", "#f4e3a1", "#7d5e22", "#9bb04a"]
    exact = vb.hex_to_rgb("#9bb04a")
    out = vb.clamp_color(exact, palette)
    assert vb.rgb_to_hex(out) == "#9bb04a", "an exact palette colour must clamp to itself"


def test_palette_clamp_green_grass_to_green_note():
    # a saturated grass green should clamp to the palette's green base note, not an amber
    palette = ["#b8862f", "#e3c46a", "#f4e3a1", "#7d5e22", "#9bb04a"]
    grass_green = vb.hex_to_rgb("#88aa33")
    out = vb.clamp_color(grass_green, palette)
    assert vb.rgb_to_hex(out) == "#9bb04a", f"expected green note, got {vb.rgb_to_hex(out)}"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    return 0 if passed == len(fns) else 1


if __name__ == "__main__":
    sys.exit(_run_all())
