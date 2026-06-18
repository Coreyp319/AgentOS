#!/usr/bin/env python3
"""Unit tests for the clip/occlusion VERDICT — PURE geometry, no Blender, no GPU.

Run:  python3 tests/test_clip_verdict.py     (from spikes/creative-env/)
   or python3 -m pytest tests/

These prove the gate's DECISIONS without a scene: the clip verdict (eye-vs-ground),
the per-kind occlusion verdict (ray-vs-hit + per-kind thresholds), and the
scene-geometry SHA-256 gate (decision-determinism, not pixels — the repo's
reproducibility doctrine). The three named cases the task asks for:
  * clean field            → no occlusion;
  * planted occluder       → REJECTED (a column/vault in the line of sight);
  * recovered-by-widen     → a near hit that, once the rail widens past it, clears.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import clip_verdict as cv  # noqa: E402
import layout  # noqa: E402


# ---------------------------------------------------------------------------
# per-kind thresholds (P2.11) — a pure function of element kind
# ---------------------------------------------------------------------------
def test_grass_is_forgiving():
    t = cv.thresholds_for_kind("grass-instances")
    # grass legitimately fills the near, low foreground -> generous near_field, high bar
    assert t["near_field"] >= 2.0
    assert t["occluder_h"] >= 1.5


def test_column_is_strict():
    t = cv.thresholds_for_kind("column")
    # a built element blocks the view as soon as it's in front of the lens
    assert t["near_field"] <= 0.5
    assert t["occluder_h"] <= 1.0


def test_unknown_kind_fails_safe_strict():
    # a kind not in the table must fall back to STRICT (catch the occluder), not lenient
    t = cv.thresholds_for_kind("totally-new-kind")
    assert t["near_field"] <= 0.5 and t["occluder_h"] <= 1.0


def test_scene_takes_strictest_occluder():
    # pure grass keeps the grass tolerance...
    grass_only = cv.thresholds_for_kinds(["grass-instances", "ground-plane"])
    assert grass_only["near_field"] == 3.0
    # ...adding a column tightens the WHOLE scene to the column's strict tolerance,
    # so a pillar can no longer hide behind the grass pass.
    with_col = cv.thresholds_for_kinds(["grass-instances", "ground-plane", "column"])
    assert with_col["near_field"] < grass_only["near_field"]
    assert with_col["occluder_h"] < grass_only["occluder_h"]


def test_ground_plane_alone_never_occludes():
    t = cv.thresholds_for_kinds(["ground-plane"])
    assert t["near_field"] == 0.0  # nothing can be flagged as an occluder


# ---------------------------------------------------------------------------
# clip verdict (eye-vs-ground)
# ---------------------------------------------------------------------------
def test_normal_ground_hit_is_not_a_clip():
    # eye at 3.5m, ray-down hits the ground at z=0 -> BELOW the eye -> fine
    assert cv.is_clip(3.5, (0.0, 10.0, 0.0)) is False


def test_eye_buried_under_mesh_is_a_clip():
    # ray-down hits geometry ABOVE the eye -> the eye is buried -> CLIP
    assert cv.is_clip(2.0, (0.0, 10.0, 4.0)) is True


def test_down_ray_miss_is_not_a_clip():
    assert cv.is_clip(3.5, None) is False


# ---------------------------------------------------------------------------
# occlusion verdict (ray-vs-hit + per-kind thresholds)
# the three named cases: clean / planted occluder / recovered-by-widen
# ---------------------------------------------------------------------------
GRASS = cv.thresholds_for_kinds(["grass-instances", "ground-plane"])
WITH_COLUMN = cv.thresholds_for_kinds(["grass-instances", "ground-plane", "column"])


def test_clean_field_no_occlusion():
    # clear line of sight (no forward hit) -> never an occlusion
    eye = (0.0, 5.0, 3.5)
    assert cv.is_occlusion(eye, None, GRASS) is False


def test_foreground_grass_is_not_an_occluder():
    # a near (1.2 m), low (0.9 m) hit in a GRASS scene is foreground clutter, not a wall
    eye = (0.0, 5.0, 3.5)
    grass_hit = (0.0, 6.2, 0.9)
    assert cv.is_occlusion(eye, grass_hit, GRASS) is False


def test_planted_column_is_rejected():
    # plant a column directly in the line of sight: a near (1.2 m), tall (3.0 m) hit.
    # in a scene that DECLARES a column, the strict threshold REJECTS it.
    eye = (0.0, 5.0, 3.5)
    column_hit = (0.0, 6.2, 3.0)
    assert cv.is_occlusion(eye, column_hit, WITH_COLUMN) is True


def test_recovered_by_widen():
    # the SAME planted-column hit, after the rail widens so the eye is shifted laterally
    # off the column and the next forward cast MISSES it -> recovered, no occlusion.
    eye_after_widen = (4.0, 5.0, 3.5)
    forward_hit_after_widen = None  # widened rail no longer points at the column
    assert cv.is_occlusion(eye_after_widen, forward_hit_after_widen, WITH_COLUMN) is False


def test_distant_tall_wall_is_an_occluder_even_for_grass():
    # a hit BEYOND the near field AND above grass height blocks the view even in a grass
    # scene -> this is exactly the pillar-clip the gate exists to kill.
    eye = (0.0, 5.0, 3.5)
    wall_hit = (0.0, 15.0, 4.0)  # 10 m ahead, 4 m tall
    assert cv.is_occlusion(eye, wall_hit, GRASS) is True


# ---------------------------------------------------------------------------
# scene-geometry SHA-256 gate (decision-determinism, not pixels)
# ---------------------------------------------------------------------------
def _canonical_inputs():
    cps = layout.trail_control_points("foreground", "horizon")
    n = 24
    samples = layout.sample_trail(cps, n)
    stations = [(x, y, layout.arc_height("level", i / (n - 1)))
                for i, (x, y, _z) in enumerate(samples)]
    thr = cv.thresholds_for_kinds(["grass-instances", "ground-plane"])
    return cps, stations, thr


# Checked-in golden hash of the canonical amber-field decision geometry. This is the
# reproducibility gate: the trail control points + the 24 validator stations + the
# per-kind thresholds must hash IDENTICALLY run-to-run. If this changes, a geometry
# DECISION changed (intended → re-pin; unintended → a determinism regression).
CANONICAL_GEOMETRY_SHA256 = (
    "2dc89fac74c1bf0248ea79ed58dd422896c350ad6d53bbb4faacb653c2459136"
)


def test_geometry_hash_matches_golden():
    cps, stations, thr = _canonical_inputs()
    got = cv.scene_geometry_sha256(cps, stations, thr)
    assert got == CANONICAL_GEOMETRY_SHA256, (
        f"scene-geometry hash drifted: {got} != {CANONICAL_GEOMETRY_SHA256}")


def test_geometry_hash_is_deterministic():
    cps, stations, thr = _canonical_inputs()
    a = cv.scene_geometry_sha256(cps, stations, thr)
    b = cv.scene_geometry_sha256(cps, stations, thr)
    assert a == b


def test_geometry_hash_changes_when_a_decision_changes():
    cps, stations, thr = _canonical_inputs()
    base = cv.scene_geometry_sha256(cps, stations, thr)
    moved = [(x + 1.0, y, z) for (x, y, z) in stations]  # shift a decision
    assert cv.scene_geometry_sha256(cps, moved, thr) != base


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
