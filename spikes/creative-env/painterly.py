#!/usr/bin/env python3
"""painterly.py — Design-0023 stage [5] post-grade (bpy compositor).

The "stylized like an oil painting" is a SYSTEM-OWNED post-grade, never a prompt
word (Design-0023: "code disposes the style, the model never prompt-words it").
The same parametric grade is reproducible + revertible.

Primary path (used here): EEVEE's compositor node tree.
  * CompositorNodeKuwahara  -> the brush-stroke / flattened-region oil look
    (anisotropic Kuwahara is the canonical painterly filter; present in 5.1.2);
  * a posterize/quantize    -> palette reduction (fewer tonal steps = paint-like);
  * a subtle canvas grain   -> a low-amp noise overlay for the canvas texture.

Because it runs in the compositor, every rendered frame carries the grade — no
separate post pass, no extra full-screen render. (A numpy fallback is provided for
environments without the Kuwahara node; this box HAS it, so the compositor path is
used.)

Style is parametric + fixed: brief['render']['style'] selects the preset; the
numbers below are the disposal, not a prompt.
"""
from __future__ import annotations

import sys

import bpy

_PRESETS = {
    "painterly": {"kuwahara_size": 6, "posterize_steps": 10, "grain": 0.025},
    "flat":      {"kuwahara_size": 3, "posterize_steps": 6,  "grain": 0.0},
    "none":      None,
}


def _log(msg: str) -> None:
    print(f"[painterly] {msg}", file=sys.stderr, flush=True)


def apply(brief: dict) -> bool:
    """Install the compositor grade for the brief's render.style. Returns True if applied.

    Blender 5.x: the scene compositor is a CompositorNodeTree node-group assigned to
    scene.compositing_node_group, wired Group-Input(render image) -> grade -> Group-Output.
    """
    style = (brief.get("render") or {}).get("style", "painterly")
    preset = _PRESETS.get(style)
    if preset is None:
        _log(f"render.style={style!r} -> no post-grade")
        return False

    scene = bpy.context.scene
    ng = bpy.data.node_groups.new("painterly_grade", "CompositorNodeTree")
    ng.interface.new_socket("Image", in_out="INPUT", socket_type="NodeSocketColor")
    ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    scene.compositing_node_group = ng
    nodes, links = ng.nodes, ng.links

    n_in = nodes.new("NodeGroupInput")
    n_out = nodes.new("NodeGroupOutput")
    cursor = n_in
    out_sock = "Image"

    if hasattr(bpy.types, "CompositorNodeKuwahara"):
        kuwahara = nodes.new("CompositorNodeKuwahara")
        # ANISOTROPIC follows local structure -> directional brush strokes (the oil look)
        if "Type" in kuwahara.inputs:
            try:
                kuwahara.inputs["Type"].default_value = "ANISOTROPIC"
            except TypeError:
                pass
        if hasattr(kuwahara, "variation"):
            try:
                kuwahara.variation = "ANISOTROPIC"
            except TypeError:
                pass
        if "Size" in kuwahara.inputs:
            kuwahara.inputs["Size"].default_value = preset["kuwahara_size"]
        if "Uniformity" in kuwahara.inputs:
            kuwahara.inputs["Uniformity"].default_value = 4
        if "Sharpness" in kuwahara.inputs:
            kuwahara.inputs["Sharpness"].default_value = 0.4
        links.new(cursor.outputs[out_sock], kuwahara.inputs["Image"])
        cursor, out_sock = kuwahara, "Image"
        _log(f"Kuwahara grade installed (size {preset['kuwahara_size']})")
    else:
        _log("CompositorNodeKuwahara absent — palette/grain only (numpy fallback covers Kuwahara)")

    # palette reduction via posterize (quantize tonal steps -> paint-like banding)
    if preset["posterize_steps"]:
        post = nodes.new("CompositorNodePosterize")
        if "Steps" in post.inputs:
            post.inputs["Steps"].default_value = preset["posterize_steps"]
        links.new(cursor.outputs[out_sock], post.inputs["Image"])
        cursor, out_sock = post, "Image"

    # canvas 'tooth' / contrast lift (a small contrast bump deepens the flattened
    # painterly regions; honest about what it is — not procedural noise). Kept gentle
    # so it shapes tone rather than clipping the amber mids to white.
    if preset["grain"] > 0.0:
        bc = nodes.new("CompositorNodeBrightContrast")
        if "Contrast" in bc.inputs:
            bc.inputs["Contrast"].default_value = 0.6
        if "Brightness" in bc.inputs:
            bc.inputs["Brightness"].default_value = -0.02
        links.new(cursor.outputs[out_sock], bc.inputs["Image"])
        cursor, out_sock = bc, "Image"

    links.new(cursor.outputs[out_sock], n_out.inputs["Image"])
    _log(f"compositor post-grade '{style}' applied")
    return True


# ---------------------------------------------------------------------------
# numpy fallback — a post pass over already-rendered frames (no Blender needed
# beyond numpy, which ships in Blender's python). Useful if the compositor path
# is unavailable; documented but not the primary route on this box.
# ---------------------------------------------------------------------------
def _stamp_failed_marker(rgb, np) -> None:
    """Paint a deterministic VALIDATOR-FAILED marker into the frame (in-place).

    A solid magenta band across the top edge — a reviewer cannot mistake a graded
    failed frame for a clean one (P0.3: the gate must be visible, not just logged).
    Magenta is outside the locked amber/green palette on purpose: it reads as
    'machine warning', not scene colour.
    """
    h = rgb.shape[0]
    band = max(2, h // 16)
    rgb[h - band:, :, 0] = 1.0   # bpy pixel rows are bottom-up; paint the visible TOP
    rgb[h - band:, :, 1] = 0.0
    rgb[h - band:, :, 2] = 1.0


def grade_frame_numpy(in_png: str, out_png: str, brief: dict, mark_failed: bool = False) -> None:
    """Apply a cheap Kuwahara-ish smoothing + posterize over a PNG using numpy.

    Loads via bpy.data.images so we don't need PIL (absent on this box). When
    `mark_failed` is set (the validator declined and even the safe fallback could not
    clear), a magenta warning band is stamped so the proposal is unmistakably flagged.
    """
    import numpy as np

    style = (brief.get("render") or {}).get("style", "painterly")
    preset = _PRESETS.get(style)
    if preset is None:
        if mark_failed:
            img = bpy.data.images.load(in_png)
            w, h = img.size
            px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
            _stamp_failed_marker(px[:, :, :3], np)
            out = bpy.data.images.new("graded", w, h, alpha=True)
            out.pixels = px.reshape(-1).tolist()
            out.filepath_raw = out_png
            out.file_format = "PNG"
            out.save()
        else:
            import shutil
            shutil.copy(in_png, out_png)
        return

    img = bpy.data.images.load(in_png)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    rgb = px[:, :, :3]

    # box-smooth (a crude region flatten -> cheap painterly read)
    k = max(1, preset["kuwahara_size"] // 2)
    pad = np.pad(rgb, ((k, k), (k, k), (0, 0)), mode="edge")
    acc = np.zeros_like(rgb)
    cnt = 0
    for dy in range(-k, k + 1):
        for dx in range(-k, k + 1):
            acc += pad[k + dy:k + dy + h, k + dx:k + dx + w, :]
            cnt += 1
    rgb = acc / cnt

    # posterize
    steps = preset["posterize_steps"]
    if steps:
        rgb = np.round(rgb * (steps - 1)) / (steps - 1)

    # grain
    if preset["grain"] > 0.0:
        rng = np.random.default_rng(0xCA11A5)  # fixed seed -> reproducible grain
        rgb = np.clip(rgb + (rng.random(rgb.shape) - 0.5) * 2 * preset["grain"], 0.0, 1.0)

    if mark_failed:
        _stamp_failed_marker(rgb, np)

    px[:, :, :3] = rgb
    out = bpy.data.images.new("graded", w, h, alpha=True)
    out.pixels = px.reshape(-1).tolist()
    out.filepath_raw = out_png
    out.file_format = "PNG"
    out.save()
    _log(f"numpy grade -> {out_png}{' [VALIDATOR-FAILED MARKER]' if mark_failed else ''}")
