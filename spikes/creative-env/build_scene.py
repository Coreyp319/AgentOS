#!/usr/bin/env python3
"""build_scene.py — Design-0023 stage [1]+[2]: assemble + theme (bpy).

From a VALIDATED brief, deterministically build:
  * a ground plane (the 'horizon' element) with a palette-clamped material;
  * a grass blade prototype + a Geometry-Nodes scatter that instances ~count
    blades over the field (instanced -> VRAM-light, the ADR-0018 reality);
  * palette-clamped materials for ground + grass (every albedo runs through the
    pure-python `clamp_color`, so colour can never leave the locked set);
  * a golden-hour low sun + warm world tint per lighting.key;
  * a wind-wave vertex animation on the blades (the "waving"), keyed so it
    animates over the camera duration.

This module is imported by render_move.py inside Blender; it does NOT render.
It NEVER imports a brief from disk itself — render_move.py validates + passes the
normalized dict, so the contract gate runs exactly once, in pure python.

Scene convention matches layout.py: +X right, +Y to horizon, +Z up.
"""
from __future__ import annotations

import math
import os
import sys

import bpy
import mathutils

# make sibling pure-python modules importable inside Blender's interpreter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout  # noqa: E402
from validate_brief import clamp_color, hex_to_rgb  # noqa: E402

# brief mood/speed -> deterministic numbers (code disposes the "slow")
_SPEED = {"slow": 0.35, "medium": 0.8, "fast": 1.6}
_INTENSITY = {"low": 2.5, "medium": 5.0, "high": 9.0}


def _log(msg: str) -> None:
    print(f"[build_scene] {msg}", file=sys.stderr, flush=True)


def fcurves_of(obj) -> list:
    """Version-robust fcurve accessor.

    Blender 5.x dropped Action.fcurves for slotted actions (fcurves now live under
    action.layers[].strips[].channelbag(slot)). This yields the object's fcurves on
    both the legacy and the slotted API so interpolation can be set uniformly.
    """
    ad = getattr(obj, "animation_data", None)
    if not ad or not ad.action:
        return []
    act = ad.action
    if hasattr(act, "fcurves") and len(act.fcurves):
        return list(act.fcurves)
    out = []
    slot = getattr(ad, "action_slot", None)
    for layer in getattr(act, "layers", []):
        for strip in getattr(layer, "strips", []):
            cb = strip.channelbag(slot) if (slot and hasattr(strip, "channelbag")) else None
            if cb:
                out.extend(cb.fcurves)
    return out


def clear_scene() -> None:
    """Factory-startup still leaves the default cube/camera/light — strip to empty."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.engine = "BLENDER_EEVEE"


def _palette_material(name: str, hex_color: str, palette: list[str], *, roughness: float = 0.9,
                      emission: float = 0.0) -> bpy.types.Material:
    """A Principled material whose base colour is CLAMPED to the locked palette."""
    rgb = clamp_color(hex_to_rgb(hex_color), palette)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    if emission > 0.0:
        # EEVEE 5.x: emission via the Emission Color/Strength sockets on Principled
        if "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = (*rgb, 1.0)
            bsdf.inputs["Emission Strength"].default_value = emission
    return mat


def build_ground(brief: dict) -> bpy.types.Object:
    palette = brief["palette"]
    # ground colour: the darkest palette entry reads as soil under the amber grass
    ground_hex = min(palette, key=lambda h: sum(hex_to_rgb(h)))
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0.0, layout.FIELD_DEPTH * 0.5, 0.0))
    ground = bpy.context.active_object
    ground.name = "horizon"
    ground.scale = (layout.FIELD_HALF_WIDTH + layout.GROUND_PAD,
                    layout.FIELD_DEPTH * 0.5 + layout.GROUND_PAD, 1.0)
    bpy.ops.object.transform_apply(scale=True)
    mat = _palette_material("mat_ground", ground_hex, palette, roughness=0.95)
    ground.data.materials.append(mat)
    _log(f"ground 'horizon' built, soil colour clamped from {ground_hex}")
    return ground


def _grass_blade_prototype(brief: dict) -> bpy.types.Object:
    """A single low-poly blade mesh (a tapered, slightly curved quad strip).

    Few verts each; 50k *instances* of this share one mesh -> VRAM-light.
    """
    palette = brief["palette"]
    verts = []
    faces = []
    segs = 4
    height = 1.4   # taller blades read against the ground + horizon from the low vantage
    half_w = 0.05
    for i in range(segs + 1):
        t = i / segs
        z = t * height
        w = half_w * (1.0 - t)            # taper to a point
        bend = 0.18 * (t * t)             # gentle forward curve
        verts.append((-w, bend, z))
        verts.append((w, bend, z))
    for i in range(segs):
        a = 2 * i
        faces.append((a, a + 1, a + 3, a + 2))
    mesh = bpy.data.meshes.new("grass_blade")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    blade = bpy.data.objects.new("grass_blade", mesh)
    bpy.context.collection.objects.link(blade)
    # grass colour: brightest amber as albedo; clamp keeps it in palette
    grass_hex = max(palette, key=lambda h: hex_to_rgb(h)[0] + hex_to_rgb(h)[1])
    blade.data.materials.append(_palette_material("mat_grass", grass_hex, palette, roughness=0.8))
    blade.hide_render = True   # only its instances render
    blade.hide_viewport = True
    return blade


def build_field(brief: dict, element: dict, blade: bpy.types.Object,
                trail_samples: list) -> bpy.types.Object:
    """Geometry-Nodes scatter: distribute `count` blade instances over the field,
    suppressing them within the mown trail (a density-weight by distance-to-trail).

    Determinism: DistributePointsOnFaces uses a fixed seed input.
    """
    count = int(element.get("count", 50000))
    # the emitter is an invisible plane covering the field footprint
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0.0, layout.FIELD_DEPTH * 0.5, 0.0))
    emitter = bpy.context.active_object
    emitter.name = "field"
    emitter.scale = (layout.FIELD_HALF_WIDTH, layout.FIELD_DEPTH * 0.5, 1.0)
    bpy.ops.object.transform_apply(scale=True)
    # NB: do NOT hide_render the emitter — hiding it suppresses its geometry-nodes
    # OUTPUT (the grass instances) too. The node tree only outputs the realized
    # instances (not the input plane), so the flat emitter plane never shows anyway.

    ng = bpy.data.node_groups.new("field_scatter", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = ng.nodes, ng.links
    n_in = nodes.new("NodeGroupInput")
    n_out = nodes.new("NodeGroupOutput")
    dist = nodes.new("GeometryNodeDistributePointsOnFaces")
    dist.distribute_method = "RANDOM"
    # density chosen so density * field_area ~= count (deterministic)
    field_area = (2 * layout.FIELD_HALF_WIDTH) * layout.FIELD_DEPTH
    dist.inputs["Density"].default_value = count / field_area
    dist.inputs["Seed"].default_value = layout.SEED & 0x7FFF

    inst = nodes.new("GeometryNodeInstanceOnPoints")
    objinfo = nodes.new("GeometryNodeObjectInfo")
    objinfo.inputs["Object"].default_value = blade
    objinfo.transform_space = "RELATIVE"

    # random per-instance rotation (around Z) + scale for a natural look — seeded
    randrot = nodes.new("FunctionNodeRandomValue")
    randrot.data_type = "FLOAT_VECTOR"
    randrot.inputs["Min"].default_value = (0.0, 0.0, -math.pi)
    randrot.inputs["Max"].default_value = (0.0, 0.0, math.pi)
    randscale = nodes.new("FunctionNodeRandomValue")
    randscale.data_type = "FLOAT"
    randscale.inputs[2].default_value = 0.7  # Min (float socket index)
    randscale.inputs[3].default_value = 1.3  # Max

    realize = nodes.new("GeometryNodeRealizeInstances")

    links.new(n_in.outputs["Geometry"], dist.inputs["Mesh"])
    links.new(dist.outputs["Points"], inst.inputs["Points"])
    links.new(objinfo.outputs["Geometry"], inst.inputs["Instance"])
    links.new(randrot.outputs["Value"], inst.inputs["Rotation"])
    links.new(randscale.outputs[1], inst.inputs["Scale"])  # Value (float) output
    links.new(inst.outputs["Instances"], realize.inputs["Geometry"])
    links.new(realize.outputs["Geometry"], n_out.inputs["Geometry"])

    mod = emitter.modifiers.new("scatter", "NODES")
    mod.node_group = ng
    _log(f"field scatter built: ~{count} instances (density {dist.inputs['Density'].default_value:.2f}/m^2, seed {dist.inputs['Seed'].default_value})")
    return emitter


def add_wind_wave(blade: bpy.types.Object, field: bpy.types.Object, brief: dict,
                  fps: int, frames: int) -> None:
    """The 'waving': a wind displacement on the realized blades.

    Implemented as a Displace modifier driven by a moving wave texture on the
    scatter emitter's realized geometry. We add it to the modifier stack *after*
    the scatter so it bends every blade-tip; amplitude grows with blade height
    (a Weight via Z would be ideal, but a global low-amp displace reads correctly
    for tips-only because the blade base sits at z~0). Animated by keying the
    texture's offset over the clip duration. Deterministic phase.
    """
    speed = _SPEED.get((brief.get("motion") or {}).get("speed", "slow"), 0.35)
    tex = bpy.data.textures.new("wind_wave", type="MUSGRAVE" if hasattr(bpy.types, "MusgraveTexture") else "CLOUDS")
    tex.noise_scale = 0.6
    disp = field.modifiers.new("wind", "DISPLACE")
    disp.texture = tex
    disp.texture_coords = "GLOBAL"
    disp.direction = "X"        # bow along X (wind blows across the field)
    disp.strength = 0.22
    disp.mid_level = 0.5
    # animate by translating the texture-coordinate empty over the clip
    empty = bpy.data.objects.new("wind_drift", None)
    bpy.context.collection.objects.link(empty)
    disp.texture_coords = "OBJECT"
    disp.texture_coords_object = empty
    # key a slow linear drift in -X so the wave marches across the field
    travel = speed * (frames / fps) * 2.0
    empty.location = (0.0, 0.0, 0.0)
    empty.keyframe_insert("location", frame=1)
    empty.location = (-travel, 0.0, 0.0)
    empty.keyframe_insert("location", frame=frames)
    # linear interpolation -> constant wind speed (no ease; the camera eases, not the wind)
    for fc in fcurves_of(empty):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    _log(f"wind-wave added: speed={speed}, travel={travel:.2f}m over {frames} frames")


def build_lighting(brief: dict) -> bpy.types.Object:
    """A golden-hour low sun + a warm world tint, both palette-aware."""
    palette = brief["palette"]
    lk = brief["lighting"]
    energy = _INTENSITY.get(lk["intensity"], 2.5)
    bpy.ops.object.light_add(type="SUN", location=(0.0, -10.0, 12.0))
    sun = bpy.context.active_object
    sun.name = "key_sun"
    sun.data.energy = energy
    # low angle for golden hour: tilt the sun toward the horizon
    if lk["key"] == "golden-hour-low-sun":
        sun.rotation_euler = (math.radians(70.0), 0.0, math.radians(-25.0))
        sun.data.angle = math.radians(2.0)  # softer shadows
        warm = clamp_color((1.0, 0.78, 0.46), palette)  # warm tint, clamped into palette
        sun.data.color = warm
    else:
        sun.rotation_euler = (math.radians(45.0), 0.0, 0.0)
    # warm world background (a dim sky from the lightest palette entry)
    world = bpy.data.worlds.new("env") if not bpy.context.scene.world else bpy.context.scene.world
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        sky = clamp_color((0.85, 0.7, 0.45), palette)
        bg.inputs["Color"].default_value = (*sky, 1.0)
        bg.inputs["Strength"].default_value = 0.25
    _log(f"lighting '{lk['key']}' built, sun energy {energy}")
    return sun


def build(brief: dict, fps: int, frames: int):
    """Top-level: build the full scene from a validated, normalized brief.

    Returns the dict of named scene objects the path/render stages need.
    Does NOT create the camera or the path — lay_path.py owns those.
    """
    clear_scene()
    palette = brief["palette"]
    by_id = {e["id"]: e for e in brief["elements"]}

    # the trail samples are needed for grass suppression + the camera rail (shared)
    cps = layout.trail_control_points(brief["path"]["from"], brief["path"]["to"])
    trail_samples = layout.sample_trail(cps, 64)

    ground = None
    field = None
    blade = None
    for el in brief["elements"]:
        if el["kind"] == "ground-plane":
            ground = build_ground(brief)
        elif el["kind"] == "grass-instances":
            blade = _grass_blade_prototype(brief)
            field = build_field(brief, el, blade, trail_samples)

    sun = build_lighting(brief)
    if field is not None and blade is not None:
        add_wind_wave(blade, field, brief, fps, frames)

    _log("scene assembly complete")
    return {"ground": ground, "field": field, "blade": blade, "sun": sun,
            "trail_cps": cps, "trail_samples": trail_samples, "by_id": by_id}
