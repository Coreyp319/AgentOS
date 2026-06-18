#!/usr/bin/env python3
"""clip_verdict.py — the clip/occlusion VERDICT, as PURE GEOMETRY (no bpy).

Design-0023 stage [4] used to bury its verdict math inside the bpy-dependent
raycast loop in `lay_path.py`, with grass-specific thresholds hardcoded and zero
unit tests. This module extracts the *decision* — given a ray-cast result, is the
station a clip / an occlusion? — into pure functions that need no Blender and can
be unit-tested on a GPU-less box.

The split is the "model proposes, code disposes" discipline applied to the gate
itself: `lay_path.py` still owns the `bpy` ray_cast (the only thing that needs the
scene), but the *verdict* — and the per-kind thresholds that drive it — live here,
where they are deterministic, inspectable, and tested.

Two verdicts:
  * CLIP — the camera eye is inside/under geometry (a ray straight DOWN from the
    eye hits something ABOVE the eye → the rail dipped under a mesh).
  * OCCLUSION — the framing line-of-sight is blocked by a TALL, NON-foreground
    occluder (a ray from the eye toward the framing point hits solid geometry that
    stands above the foreground-clutter height, beyond the near field).

Per-kind thresholds (item P2.11): grass legitimately fills the lower frame, so a
grass hit that is near + low is foreground, NOT an occluder. A `column`/`vault`
does NOT get that pass — a near, low pillar hit is exactly the clip we must catch.
`thresholds_for_kinds` is a PURE FUNCTION of the brief's element kinds.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Iterable, Sequence

Vec3 = Sequence[float]


# ---------------------------------------------------------------------------
# per-kind occlusion thresholds (P2.11) — a pure function of element kind
# ---------------------------------------------------------------------------
# Each kind declares how forgiving the occlusion check is of a line-of-sight hit:
#   near_field   — metres; a hit closer than this is "foreground clutter" for this
#                  kind and is NOT counted as an occluder.
#   occluder_h   — metres; a hit must stand ABOVE this to count as a solid occluder.
#                  (a low hit below this is ground/foliage, not a view-blocking wall)
# A SCATTER of grass blades fills the foreground (large near_field, only a tall hit
# blocks). A built element (column / vault / rock / tree) is a real occluder the
# moment it stands in the line of sight — tiny near_field, ~ground-level occluder_h —
# so a pillar planted in front of the lens is correctly REJECTED.
_KIND_THRESHOLDS: dict[str, dict[str, float]] = {
    # forgiving: grass legitimately fills the near, low foreground
    "grass-instances": {"near_field": 3.0, "occluder_h": 1.8},
    "particle":        {"near_field": 3.0, "occluder_h": 1.8},
    "ground-plane":    {"near_field": 0.0, "occluder_h": 1.8},  # never an occluder
    # strict: a built/solid element blocks the view as soon as it is in front of it
    "column":          {"near_field": 0.3, "occluder_h": 0.5},
    "vault":           {"near_field": 0.3, "occluder_h": 0.5},
    "rock":            {"near_field": 0.5, "occluder_h": 0.4},
    "tree":            {"near_field": 0.8, "occluder_h": 0.6},
}

# Used when a kind is unknown (defensive: the validator already allowlists kinds,
# but a future kind added to the schema before this table should fail SAFE = strict,
# so an un-tabled occluder is caught, not waved through).
_DEFAULT_THRESHOLDS = {"near_field": 0.3, "occluder_h": 0.5}


def thresholds_for_kind(kind: str) -> dict[str, float]:
    """The occlusion thresholds for a single element kind. Pure; total."""
    return dict(_KIND_THRESHOLDS.get(kind, _DEFAULT_THRESHOLDS))


def thresholds_for_kinds(kinds: Iterable[str]) -> dict[str, float]:
    """Combine per-kind thresholds into the scene's effective thresholds.

    A scene is only as forgiving as its STRICTEST occluding element: we take the
    MIN near_field and MIN occluder_h across the kinds present (excluding the
    ground plane, which is never an occluder). So a brief that is pure grass keeps
    the generous grass tolerance; the moment a column is added, the check tightens
    to the column's strict tolerance and a pillar-clip can no longer hide behind
    the grass pass. Pure function of the element kinds — no bpy, no scene.
    """
    occluding = [k for k in kinds if k != "ground-plane"]
    if not occluding:
        # only a ground plane (or empty): nothing can occlude — keep grass-generous
        return dict(_KIND_THRESHOLDS["ground-plane"])
    near = min(thresholds_for_kind(k)["near_field"] for k in occluding)
    occ = min(thresholds_for_kind(k)["occluder_h"] for k in occluding)
    return {"near_field": near, "occluder_h": occ}


# ---------------------------------------------------------------------------
# the verdicts — pure geometry (eye-vs-bbox, ray-vs-segment)
# ---------------------------------------------------------------------------
def is_clip(eye_z: float, down_hit: Vec3 | None, slack: float = 0.01) -> bool:
    """CLIP verdict: is the eye inside/under geometry?

    `down_hit` is where a ray cast straight DOWN from the eye first hit geometry
    (or None for a miss). A normal ground hit is BELOW the eye and is fine; a hit
    ABOVE the eye means the eye is buried under a mesh. Pure: no bpy.
    """
    if down_hit is None:
        return False
    return down_hit[2] > eye_z + slack


def is_occlusion(eye: Vec3, fwd_hit: Vec3 | None, thresholds: dict[str, float]) -> bool:
    """OCCLUSION verdict: is the framing line-of-sight blocked by a real occluder?

    `fwd_hit` is where a ray cast from the eye toward the framing point first hit
    geometry (or None for a clear line of sight). A hit only counts as an occluder
    if it is BEYOND the near field AND stands ABOVE the occluder height — both are
    per-kind thresholds. Foreground clutter (near + low, e.g. grass) is ignored.
    Pure: no bpy.
    """
    if fwd_hit is None:
        return False
    hit_dist = math.dist(tuple(eye), tuple(fwd_hit))
    return hit_dist > thresholds["near_field"] and fwd_hit[2] > thresholds["occluder_h"]


# ---------------------------------------------------------------------------
# decision-determinism gate — hash the SCENE GEOMETRY (not the pixels)
# ---------------------------------------------------------------------------
def scene_geometry_sha256(
    control_points: Sequence[Vec3],
    stations: Sequence[Vec3],
    thresholds: dict[str, float],
    *,
    ndigits: int = 6,
) -> str:
    """A reproducibility hash over the DECISION geometry, not the rendered pixels.

    Per the repo's reproducibility doctrine (README 'What's deterministic'): EEVEE
    AA makes the PNGs non-byte-reproducible, so a golden gate must hash the
    geometry that the DECISIONS are made from — the trail control points, the
    sampled rail stations the validator walks, and the per-kind thresholds it
    judges them with. Coordinates are rounded to `ndigits` so float formatting is
    stable across platforms; the hash is over canonical JSON.
    """
    def r(seq):
        return [[round(float(c), ndigits) for c in p] for p in seq]

    payload = {
        "control_points": r(control_points),
        "stations": r(stations),
        "thresholds": {k: round(float(v), ndigits) for k, v in sorted(thresholds.items())},
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
