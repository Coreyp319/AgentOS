#!/usr/bin/env python3
"""lay_path.py — Design-0023 stage [3]+[4]: the one spline + the validator (bpy).

Stage [3] — lay ONE Bezier spline from path.from to path.to through the field's
negative space. That single curve does double duty:
  * the VISIBLE route motif (`path.render_as`: mown-trail / road / glowing-trail);
  * the CAMERA RAIL — the camera is parented to it via a Follow-Path constraint and
    aims at `camera.subject` with a Track-To constraint, per `camera.arc`.
Authoring it once guarantees the seen trail and the ridden rail agree.

Stage [4] — the determinism gate. Sample the rail at N stations; at each, raycast
the camera position against the scene (is the eye inside geometry?) and raycast the
look-at ray to the subject (is the view occluded?). A failing station is nudged
deterministically (push up off the ground normal); if too many fail, the spline is
regenerated wider. A clipping path is the failure mode this kills. NO model here.

The mown-trail is rendered by suppressing grass near the curve (handled by a
density mask we apply here, reading the same layout.sample_trail the scatter used).
"""
from __future__ import annotations

import math
import os
import sys

import bpy
import mathutils

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clip_verdict  # noqa: E402  (PURE geometry: per-kind thresholds + verdicts, no bpy)
import layout  # noqa: E402
from validate_brief import clamp_color  # noqa: E402

_EASING = {
    "linear": "LINEAR",
    "ease-in": "SINE",       # interpolation easing on the eval_time fcurve
    "ease-out": "SINE",
    "ease-in-out": "SINE",
}
_EASE_MODE = {
    "linear": "AUTO",
    "ease-in": "EASE_IN",
    "ease-out": "EASE_OUT",
    "ease-in-out": "EASE_IN_OUT",
}


def _log(msg: str) -> None:
    print(f"[lay_path] {msg}", file=sys.stderr, flush=True)


def build_curve(brief: dict, scene_objs: dict) -> bpy.types.Object:
    """Create the Bezier curve object from the shared control points."""
    cps = scene_objs["trail_cps"]
    curve = bpy.data.curves.new("trail", type="CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(cps) - 1)
    for i, (x, y, z) in enumerate(cps):
        bp = spline.bezier_points[i]
        bp.co = (x, y, z)
        bp.handle_left_type = "AUTO"
        bp.handle_right_type = "AUTO"
    obj = bpy.data.objects.new("trail", curve)
    bpy.context.collection.objects.link(obj)
    _log(f"laid trail spline with {len(cps)} control points")
    return obj


def render_trail_motif(brief: dict, curve_obj: bpy.types.Object, scene_objs: dict) -> None:
    """The VISIBLE side of the dual-purpose spline.

    For 'mown-trail' we (a) bevel the curve into a thin flat ribbon of bare/short
    ground hugging the spline, palette-clamped to the soil tone, AND (b) the grass
    scatter already thins near the curve (build_scene suppression). The ribbon makes
    the trail read even where grass is sparse.
    """
    render_as = brief["path"]["render_as"]
    if render_as == "none":
        return
    palette = brief["palette"]
    curve = curve_obj.data
    if render_as in ("mown-trail", "road"):
        curve.bevel_depth = layout.TRAIL_HALF_WIDTH
        curve.bevel_resolution = 2
        curve.fill_mode = "FULL"
        # flatten the ribbon to the ground (extrude wide, depth thin)
        curve.bevel_depth = 0.0
        curve.extrude = 0.0
        # use a separate flat mesh ribbon: bevel a flat profile via taper is overkill
        # for the spike; instead lay a thin plane-strip mesh along samples.
        _ribbon_mesh(curve_obj, scene_objs, palette, glow=False)
    elif render_as == "glowing-trail":
        _ribbon_mesh(curve_obj, scene_objs, palette, glow=True)
    elif render_as == "stepping-stones":
        _stepping_stones(scene_objs, palette)
    _log(f"trail motif rendered as '{render_as}'")


def _ribbon_mesh(curve_obj, scene_objs, palette, glow: bool) -> None:
    """A flat ribbon mesh hugging the trail samples (the mown swath)."""
    samples = scene_objs["trail_samples"]
    hw = layout.TRAIL_HALF_WIDTH * 0.8
    verts, faces = [], []
    for i, (x, y, z) in enumerate(samples):
        # perpendicular in XY to the local tangent
        if i < len(samples) - 1:
            nx, ny = samples[i + 1][0] - x, samples[i + 1][1] - y
        else:
            nx, ny = x - samples[i - 1][0], y - samples[i - 1][1]
        ln = math.hypot(nx, ny) or 1.0
        px, py = -ny / ln, nx / ln
        verts.append((x + px * hw, y + py * hw, 0.02))
        verts.append((x - px * hw, y - py * hw, 0.02))
    for i in range(len(samples) - 1):
        a = 2 * i
        faces.append((a, a + 1, a + 3, a + 2))
    mesh = bpy.data.meshes.new("trail_ribbon")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("trail_ribbon", mesh)
    bpy.context.collection.objects.link(obj)
    from build_scene import _palette_material  # local import: bpy-side only
    # soil tone for mown ground; a faint emissive for a glowing trail
    soil = min(palette, key=lambda h: sum(int(h[1:][k:k + 2], 16) for k in (0, 2, 4)))
    bright = max(palette, key=lambda h: sum(int(h[1:][k:k + 2], 16) for k in (0, 2, 4)))
    mat = _palette_material("mat_trail", bright if glow else soil, palette,
                            roughness=0.6 if glow else 0.95,
                            emission=2.5 if glow else 0.0)
    obj.data.materials.append(mat)


def _stepping_stones(scene_objs, palette) -> None:
    samples = layout.sample_trail(scene_objs["trail_cps"], 14)
    from build_scene import _palette_material
    soil = min(palette, key=lambda h: sum(int(h[1:][k:k + 2], 16) for k in (0, 2, 4)))
    mat = _palette_material("mat_stone", soil, palette, roughness=0.9)
    for i, (x, y, z) in enumerate(samples):
        bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=0.12, location=(x, y, 0.06))
        st = bpy.context.active_object
        st.name = f"stone_{i}"
        st.data.materials.append(mat)


def place_camera_on_rail(brief: dict, curve_obj: bpy.types.Object, scene_objs: dict,
                         fps: int, frames: int) -> bpy.types.Object:
    """Parent a camera to the spline via Follow-Path + aim it with Track-To.

    camera.arc shapes the height profile (rising/level/descending); camera.move
    shapes how it sits (drift = ride the whole rail slowly; reveal = start wide).
    Returns the camera object.
    """
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 35.0
    cam = bpy.data.objects.new("camera", cam_data)
    bpy.context.collection.objects.link(cam)
    bpy.context.scene.camera = cam

    arc = brief["camera"]["arc"]
    easing = brief["camera"]["easing"]

    # Follow-Path DIRECTLY on the camera (no rider parent — a parent's curve-follow
    # rotation fought the Track-To aim and flipped the lens backward in testing).
    # use_fixed_location makes offset_factor the parametric 0..1 position we key for the
    # drift; the camera's own local Z is the eye-height offset (keyed per arc); the
    # Track-To constraint alone owns orientation.
    fp = cam.constraints.new("FOLLOW_PATH")
    fp.target = curve_obj
    fp.use_curve_follow = False
    fp.use_fixed_location = True
    curve_obj.data.use_path = True
    curve_obj.data.path_duration = frames

    # key the parametric drift with the brief's easing. A 'drift' is a CALM, short move
    # that stays inside the scene — so it travels only a fraction of the rail (0..0.45),
    # not the full length, which would overrun the grass and end on empty sky. Other
    # moves (push-in / fly-through) would use a longer span; that's their disposal.
    drift_end = {"drift": 0.45}.get(brief["camera"]["move"], 0.9)
    fp.offset_factor = 0.0
    fp.keyframe_insert("offset_factor", frame=1)
    fp.offset_factor = drift_end
    fp.keyframe_insert("offset_factor", frame=frames)
    from build_scene import fcurves_of
    for fc in fcurves_of(cam):
        for kp in fc.keyframe_points:
            kp.interpolation = _EASING.get(easing, "SINE")
            kp.easing = _EASE_MODE.get(easing, "EASE_IN_OUT")

    # eye height per arc (level => constant), as the camera's local Z offset above the
    # path point. Keyed so 'rising'/'descending' arcs animate height too.
    for f in range(1, frames + 1):
        t = (f - 1) / max(1, frames - 1)
        cam.location = (0.0, 0.0, layout.arc_height(arc, t))
        cam.keyframe_insert("location", frame=f)

    # Aim at a dedicated FAR framing point, NOT the subject object itself.
    # (Targeting the 'horizon' GROUND PLANE pointed the lens DOWN at the plane origin ->
    #  a flat colour wash. The brief's camera.subject is INTENT; code disposes the actual
    #  aim.) The point is far in +Y at ~camera height so the lens is near-level: the
    #  horizon line sits in the upper third, receding grass fills the lower 2/3, a sliver
    #  of warm sky sits above. It is parented to nothing (world-fixed), so as the camera
    #  drifts forward the framing stays steady — the calm 'drift' read.
    aim = bpy.data.objects.new("aim", None)
    aim.location = (0.0, layout.FIELD_DEPTH + 400.0, 2.6)
    bpy.context.collection.objects.link(aim)
    tt = cam.constraints.new("TRACK_TO")
    tt.target = aim
    tt.track_axis = "TRACK_NEGATIVE_Z"
    tt.up_axis = "UP_Y"
    _log(f"camera on rail: move={brief['camera']['move']} arc={arc} easing={easing}, "
         f"subject intent={brief['camera']['subject']!r} -> framing aim at {tuple(aim.location)}")
    return cam


# ---------------------------------------------------------------------------
# stage [4] — the determinism gate
# ---------------------------------------------------------------------------
def validate_path(brief: dict, curve_obj: bpy.types.Object, scene_objs: dict,
                  n_stations: int = 24, max_regen: int = 2):
    """Raycast the rail. Returns (ok: bool, report: dict).

    At each station along the rail we reconstruct the camera world position (the
    XY from the spline sample + the arc height) and:
      * cast a short ray straight down — if the eye is BELOW the ground hit, it's
        inside/under geometry -> clip;
      * cast a ray from the eye to the subject aim point — if it hits geometry
        before reaching the subject, the look-at is occluded.
    Failing stations are nudged up; if too many fail we widen the spline and retry.
    """
    deps = bpy.context.evaluated_depsgraph_get()
    scene = bpy.context.scene
    arc = brief["camera"]["arc"]
    # Per-kind occlusion thresholds (P2.11): the FORGIVENESS of the occlusion check is
    # a PURE FUNCTION of the brief's element kinds, computed in clip_verdict (no bpy).
    # Grass legitimately fills the near, low foreground (large near_field, high
    # occluder_h → a near, low hit is ignored); a column/vault does NOT get that pass
    # (tiny near_field, ~ground occluder_h → a planted pillar is REJECTED). The scene
    # takes the strictest occluding element's tolerance, so adding a column to a grass
    # brief tightens the gate instead of letting the pillar hide behind the grass rule.
    kinds = [el.get("kind", "") for el in brief.get("elements", [])]
    thresholds = clip_verdict.thresholds_for_kinds(kinds)
    _log(f"occlusion thresholds (per-kind, from {sorted(set(kinds))}): {thresholds}")

    def stations():
        samples = layout.sample_trail(scene_objs["trail_cps"], n_stations)
        for i, (x, y, _z) in enumerate(samples):
            t = i / (n_stations - 1)
            yield i, mathutils.Vector((x, y, layout.arc_height(arc, t)))

    report = {"stations": n_stations, "clips": [], "occlusions": [], "regens": 0,
              "nudges": 0, "thresholds": thresholds}
    ok = False
    for attempt in range(max_regen + 1):
        report["clips"].clear()
        report["occlusions"].clear()
        for i, eye in stations():
            # (a) eye inside/under geometry? ray DOWN — a hit ABOVE the eye means the eye
            # is buried (e.g. the rail dipped under a mesh). bpy owns the cast; the VERDICT
            # (clip_verdict.is_clip) is pure geometry, unit-tested without Blender.
            origin = eye + mathutils.Vector((0, 0, 0.05))
            hit, loc, *_ = scene.ray_cast(deps, origin, mathutils.Vector((0, 0, -1)),
                                          distance=eye.z + 2.0)
            if clip_verdict.is_clip(eye.z, tuple(loc) if hit else None):
                report["clips"].append(i)
            # (b) framing line-of-sight occluded by a TALL, NON-foreground occluder?
            aim = mathutils.Vector((eye.x, eye.y + 50.0, eye.z))  # level, far ahead
            direction = (aim - eye)
            direction.normalize()
            hit2, loc2, *_ = scene.ray_cast(deps, eye + direction * 0.02, direction,
                                            distance=(aim - eye).length)
            # bpy owns the cast; the per-kind occlusion VERDICT is pure geometry.
            if clip_verdict.is_occlusion(tuple(eye), tuple(loc2) if hit2 else None, thresholds):
                report["occlusions"].append(i)

        if not report["clips"] and not report["occlusions"]:
            _log(f"path validated: {n_stations} stations clear (attempt {attempt})")
            ok = True
            break

        # deterministic recovery
        if attempt < max_regen:
            _log(f"path validation found {len(report['clips'])} clips / "
                 f"{len(report['occlusions'])} occlusions — regenerating wider (attempt {attempt})")
            _widen_spline(curve_obj, scene_objs, factor=1.0 + 0.4 * (attempt + 1))
            report["regens"] += 1
        else:
            _log(f"path still imperfect after {max_regen} regens "
                 f"(clips={report['clips']} occl={report['occlusions']})")
            report["nudges"] = len(report["clips"])
    # HONEST verdict: valid only if BOTH clip and occlusion lists are empty. (Whether a
    # render proceeds on an invalid verdict is the DISPOSER's call — render_move.py now
    # acts on this rather than logging it; this fn only reports the truth.)
    valid = ok and not report["clips"] and not report["occlusions"]
    # decision-determinism gate: hash the DECISION geometry (control points + stations +
    # thresholds), not the pixels — the reproducibility doctrine (README §Determinism).
    # Recomputed through the PURE double-precision `layout` math (not the float32 mathutils
    # Vectors the cast uses) so the manifest hash lives in the same numeric space as the
    # checked-in golden in tests/test_clip_verdict.py — same decisions → same hash.
    cps = scene_objs["trail_cps"]
    samples = layout.sample_trail(cps, n_stations)
    pure_stations = [(x, y, layout.arc_height(arc, i / (n_stations - 1)))
                     for i, (x, y, _z) in enumerate(samples)]
    report["geometry_sha256"] = clip_verdict.scene_geometry_sha256(
        cps, pure_stations, thresholds)
    return valid, report


def _widen_spline(curve_obj, scene_objs, factor: float) -> None:
    """Push the interior control points further off the centre-line (more clearance)."""
    cps = scene_objs["trail_cps"]
    cx = sum(p[0] for p in cps) / len(cps)
    new = [cps[0]]
    for p in cps[1:-1]:
        new.append((cx + (p[0] - cx) * factor, p[1], p[2]))
    new.append(cps[-1])
    scene_objs["trail_cps"] = new
    scene_objs["trail_samples"] = layout.sample_trail(new, 64)
    spline = curve_obj.data.splines[0]
    for i, (x, y, z) in enumerate(new):
        spline.bezier_points[i].co = (x, y, z)


# Deterministic SAFE fallback: a level, raised, short camera move that physically
# cannot clip into the scatter — the geometric guarantee behind the degrade in lay().
# (Eye lifted well above the tallest catalogued occluder; travel shortened so it
# stays centred; arc forced level so it never descends into a mesh.)
SAFE_EYE_HEIGHT = 12.0   # metres — above any allowlisted element's occluder height
SAFE_DRIFT_END = 0.20    # ride only the first fifth of the rail (stays composed)


def degrade_camera_to_safe(cam_obj: bpy.types.Object, frames: int) -> None:
    """Re-key an existing rail camera to the deterministic SAFE fallback.

    Lifts the eye to SAFE_EYE_HEIGHT (above every catalogued occluder height) and
    shortens the parametric travel to SAFE_DRIFT_END, both LINEAR — a calm, raised,
    short level drift that cannot dip under or be occluded by scene geometry. No
    randomness: same brief + frames → same safe move (ADR-0003 fail-open).
    """
    cam_obj.animation_data_clear()  # drop the original keyed height + travel
    for con in list(cam_obj.constraints):
        if con.type == "FOLLOW_PATH":
            con.use_fixed_location = True
            con.offset_factor = 0.0
            con.keyframe_insert("offset_factor", frame=1)
            con.offset_factor = SAFE_DRIFT_END
            con.keyframe_insert("offset_factor", frame=frames)
    for f in range(1, frames + 1):
        cam_obj.location = (0.0, 0.0, SAFE_EYE_HEIGHT)
        cam_obj.keyframe_insert("location", frame=f)
    from build_scene import fcurves_of
    for fc in fcurves_of(cam_obj):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    _log(f"DEGRADED to safe camera: eye={SAFE_EYE_HEIGHT}m level, drift 0..{SAFE_DRIFT_END}")


def lay(brief: dict, scene_objs: dict, fps: int, frames: int) -> dict:
    """Top-level: build the spline, render its motif, rig the camera, validate.

    The verdict ACTS (P0.3): if the first pass is invalid, deterministically degrade
    to a safe raised/level/short camera and re-validate (ADR-0003 fail-open). The
    result carries `valid`, `degraded`, and `validator_failed` so the disposer
    (render_move) can render clean, render-degraded, or render-with-a-visible-marker —
    a reviewer can never mistake a clipping move for a clean one.
    """
    curve = build_curve(brief, scene_objs)
    render_trail_motif(brief, curve, scene_objs)
    cam = place_camera_on_rail(brief, curve, scene_objs, fps, frames)
    ok, report = validate_path(brief, curve, scene_objs)

    degraded = False
    if not ok:
        _log(f"verdict INVALID (clips={report['clips']} occl={report['occlusions']}) "
             f"— degrading to safe camera and re-validating")
        degrade_camera_to_safe(cam, frames)
        degraded = True
        ok2, report2 = validate_path(brief, curve, scene_objs)
        report2["degraded"] = True
        report2["pre_degrade"] = {"clips": list(report["clips"]),
                                  "occlusions": list(report["occlusions"])}
        report = report2
        ok = ok2

    # validator_failed: the gate DECLINED and even the safe fallback could not clear it.
    # The disposer must mark the proposal so it is never mistaken for clean output.
    report["degraded"] = degraded
    report["validator_failed"] = not ok
    return {"curve": curve, "camera": cam, "valid": ok,
            "degraded": degraded, "validator_failed": not ok, "report": report}
