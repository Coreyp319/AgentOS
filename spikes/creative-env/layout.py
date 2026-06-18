#!/usr/bin/env python3
"""layout.py — deterministic scene geometry, PURE PYTHON (no bpy).

The single source of truth for *where things are* in scene space, shared by
build_scene.py (the field + ground) and lay_path.py (the trail/camera rail).
Because both stages read the same numbers, "the thing you see (the trail) and
the thing the camera rides agree" by construction (Design-0023 §[3]).

Scene convention: +X right, +Y into the distance (toward horizon), +Z up.
Camera looks down +Y. Field is centred on the origin in X, runs from y=0
(foreground) to y=FIELD_DEPTH (horizon).

Everything here is a pure function of the brief + a fixed seed, so the layout
is reproducible and unit-testable without Blender.
"""
from __future__ import annotations

import math

SEED = 0xA3BEED  # fixed — the whole spike is deterministic (Design-0023 non-negotiable)

# Field extents in metres. A "field"-scale scatter element.
FIELD_HALF_WIDTH = 30.0   # X in [-30, 30]
FIELD_DEPTH = 60.0        # Y in [0, 60]; horizon plane sits beyond
GROUND_PAD = 40.0         # ground plane extends past the field so no edge is visible

# The mown trail is a swath of cleared/short grass the camera rides along.
TRAIL_HALF_WIDTH = 1.6    # metres; grass is suppressed within this of the spline


def anchor_xy(name: str) -> tuple[float, float]:
    """Resolve a path/camera abstract anchor to an (x, y) ground coordinate.

    These are the from/to allowlist values the validator already accepted.
    """
    table = {
        "foreground": (0.0, 2.0),
        "horizon":    (0.0, FIELD_DEPTH - 4.0),
        "center":     (0.0, FIELD_DEPTH * 0.5),
        "left":       (-FIELD_HALF_WIDTH * 0.6, FIELD_DEPTH * 0.5),
        "right":      (FIELD_HALF_WIDTH * 0.6, FIELD_DEPTH * 0.5),
        "entrance":   (0.0, 1.0),
        "nave":       (0.0, FIELD_DEPTH - 4.0),
        "subject":    (0.0, FIELD_DEPTH - 4.0),
    }
    return table.get(name, (0.0, FIELD_DEPTH * 0.5))


def trail_control_points(p_from: str, p_to: str) -> list[tuple[float, float, float]]:
    """The Bezier control points of the ONE spline (trail motif + camera rail).

    A gentle S through the field's negative space: it bows off the centre-line so
    the drift reads as a path *through* the space, then settles toward the horizon
    anchor. Deterministic; chosen so the camera (which rides it) frames the horizon.
    Returns world (x, y, z) with z = ground height (0) for the trail; the camera
    rail height is applied in lay_path.py per camera.arc.
    """
    x0, y0 = anchor_xy(p_from)
    x1, y1 = anchor_xy(p_to)
    span = y1 - y0
    # four control points; the lateral bows are a fixed fraction of half-width so
    # the S is reproducible and stays well inside the field.
    bow = FIELD_HALF_WIDTH * 0.28
    pts = [
        (x0,            y0,              0.0),
        (x0 - bow,      y0 + span * 0.33, 0.0),
        (x1 + bow,      y0 + span * 0.66, 0.0),
        (x1,            y1,              0.0),
    ]
    return pts


def _bezier_point(p0, p1, p2, p3, t: float) -> tuple[float, float, float]:
    mt = 1.0 - t
    a = mt * mt * mt
    b = 3 * mt * mt * t
    c = 3 * mt * t * t
    d = t * t * t
    return tuple(a * p0[i] + b * p1[i] + c * p2[i] + d * p3[i] for i in range(3))


def sample_trail(cps: list[tuple[float, float, float]], n: int) -> list[tuple[float, float, float]]:
    """Sample the cubic Bezier defined by `cps` at n evenly-spaced t in [0,1].

    Used by the validator (lay_path.py) to walk stations along the rail and by the
    grass suppression test (grass within TRAIL_HALF_WIDTH of the curve is mown).
    """
    p0, p1, p2, p3 = cps
    return [_bezier_point(p0, p1, p2, p3, i / (n - 1)) for i in range(n)]


def distance_to_polyline(px: float, py: float, poly: list[tuple[float, float, float]]) -> float:
    """Min distance (in the XY plane) from a point to a sampled polyline."""
    best = float("inf")
    for i in range(len(poly) - 1):
        ax, ay = poly[i][0], poly[i][1]
        bx, by = poly[i + 1][0], poly[i + 1][1]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        if seg2 == 0.0:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
        cx, cy = ax + t * dx, ay + t * dy
        d = math.hypot(px - cx, py - cy)
        if d < best:
            best = d
    return best


# camera arc -> a height profile along the rail (z added to the ground spline)
def arc_height(arc: str, t: float) -> float:
    """Camera eye height above ground at normalized station t in [0,1].

    Raised above strict eye height so the camera looks ACROSS the field and the
    grass blades read against the ground + a sliver of horizon sky, rather than
    edge-on at 1.7m where the field is a flat colour wash. ~3.5m is a low, gentle
    'walking a rise' vantage that keeps the pastoral feel.
    """
    base = 3.5  # vantage height, metres
    if arc == "rising":
        return base + 6.0 * t
    if arc == "descending":
        return base + 6.0 * (1.0 - t)
    return base  # level


if __name__ == "__main__":
    # quick self-check (no bpy)
    cps = trail_control_points("foreground", "horizon")
    samples = sample_trail(cps, 8)
    print("trail control points:", cps)
    print("8 stations:")
    for s in samples:
        print("  (%.2f, %.2f, %.2f)" % s)
    # a point on the centre-line near the curve should be 'mown'
    d = distance_to_polyline(0.0, 30.0, sample_trail(cps, 64))
    print("dist from (0,30) to trail = %.3f (mown if < %.2f)" % (d, TRAIL_HALF_WIDTH))
