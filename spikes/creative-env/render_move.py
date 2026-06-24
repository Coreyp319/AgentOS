#!/usr/bin/env python3
"""render_move.py — the in-Blender entrypoint (run via `blender --background --python`).

Orchestrates the whole Phase-0 loop INSIDE one Blender process:
  [0] validate the brief (the single contract gate — pure python, raises on reject)
  [1+2] build_scene.build : ground + grass scatter + lighting + wind-wave
  [3+4] lay_path.lay      : the dual-purpose spline + camera rig + raycast validator
  [5] painterly.apply     : the system-owned compositor post-grade
      then render the drift to frames + an mp4, and save the .blend for reproducibility.

Args after `--` (argparse-free to keep it Blender-safe):
  --brief PATH     (required) the brief JSON to validate + build
  --out   DIR      (required) output directory for frames/ + move.mp4 + scene.blend
  --fps   N        frames per second (default 12 — low fps is fine for the spike)
  --seconds F      override duration; default = brief.camera.duration_s
  --res   N        square-ish render: width; height = width*9//16 (default 640)
  --quick          render only 6 frames (a fast smoke render, not the full move)
  --no-grade       skip the painterly post-grade (for the idle/diff comparison)

Everything is deterministic given the same brief + flags (fixed seed in layout.py).
"""
from __future__ import annotations

import json
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_scene  # noqa: E402
import lay_path  # noqa: E402
import painterly  # noqa: E402
from validate_brief import validate  # noqa: E402


def _log(msg: str) -> None:
    print(f"[render_move] {msg}", file=sys.stderr, flush=True)


def _argv_after_dashes() -> list[str]:
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def _get(args: list[str], flag: str, default=None):
    if flag in args:
        i = args.index(flag)
        return args[i + 1] if i + 1 < len(args) else default
    return default


def _has(args: list[str], flag: str) -> bool:
    return flag in args


def setup_eevee(scene, res_x: int, res_y: int, fps: int, frames: int) -> None:
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = res_x
    scene.render.resolution_y = res_y
    scene.render.resolution_percentage = 100
    scene.render.fps = fps
    scene.frame_start = 1
    scene.frame_end = frames
    # EEVEE quality knobs that read as 'calm/abstract' and stay cheap. taa_reprojection
    # OFF removes the temporal jitter that otherwise dithers pixels run-to-run; the SCENE
    # geometry is fully deterministic (fixed seed -> identical instance positions, proven
    # by hashing the evaluated mesh), and the painterly numpy grade uses a fixed RNG seed,
    # so the only residual variance is EEVEE's own AA sampling — minimized here, not a
    # pipeline-decision nondeterminism.
    ee = scene.eevee
    for attr, val in (("taa_render_samples", 16), ("use_gtao", True),
                      ("use_taa_reprojection", False),
                      ("use_bloom", True), ("bloom_intensity", 0.02)):
        if hasattr(ee, attr):
            try:
                setattr(ee, attr, val)
            except (TypeError, AttributeError):
                pass
    # Tone: keep AgID/Standard so the locked amber palette renders as amber. Filmic
    # desaturates + washes the mid-amber toward white once the posterize/contrast grade
    # stacks on top (observed). A small negative exposure protects the highlights so the
    # painterly posterize has tonal range to work with instead of clipping to white.
    for vt in ("Standard", "Khronos PBR Neutral", "AgX"):
        try:
            scene.view_settings.view_transform = vt
            break
        except (TypeError, AttributeError):
            continue
    try:
        scene.view_settings.exposure = -0.6
    except (TypeError, AttributeError):
        pass


def main() -> int:
    args = _argv_after_dashes()
    brief_path = _get(args, "--brief")
    out_dir = _get(args, "--out")
    if not brief_path or not out_dir:
        _log("usage: -- --brief PATH --out DIR [--fps N --seconds F --res N --quick --no-grade]")
        return 2

    fps = int(_get(args, "--fps", "12"))
    res_x = int(_get(args, "--res", "640"))
    res_y = (res_x * 9) // 16
    quick = _has(args, "--quick")
    no_grade = _has(args, "--no-grade")

    # [0] the contract gate — the ONLY validation, pure python
    with open(brief_path) as f:
        raw = json.load(f)
    brief = validate(raw)   # raises BriefError on an off-vocabulary brief
    _log(f"brief VALID: {brief['theme']!r}")

    seconds = float(_get(args, "--seconds", str(brief["camera"]["duration_s"])))
    frames = 6 if quick else max(2, int(round(seconds * fps)))
    _log(f"plan: {frames} frames @ {fps}fps ({frames / fps:.1f}s), {res_x}x{res_y}, "
         f"grade={'off' if no_grade else 'on'}")

    # [1][2] assemble + theme
    scene_objs = build_scene.build(brief, fps, frames)

    # [3][4] the spline + camera + validator — the verdict now ACTS (P0.3):
    # lay() deterministically degrades to a safe camera on an invalid first pass and
    # re-validates; validator_failed means even the safe fallback could not clear, so
    # the frames get a visible marker + the manifest is stamped. A gate, not a report.
    path_result = lay_path.lay(brief, scene_objs, fps, frames)
    validator_failed = bool(path_result.get("validator_failed"))
    degraded = bool(path_result.get("degraded"))
    _log(f"path valid={path_result['valid']} degraded={degraded} "
         f"validator_failed={validator_failed} report={json.dumps(path_result['report'])}")
    if validator_failed:
        _log("GATE DECLINED: validator failed AND safe fallback failed — "
             "frames will carry a VALIDATOR-FAILED marker; do NOT treat as clean output")
    elif degraded:
        _log("GATE ACTED: original move invalid → rendered the deterministic SAFE camera")

    # render setup
    scene = bpy.context.scene
    setup_eevee(scene, res_x, res_y, fps, frames)

    os.makedirs(out_dir, exist_ok=True)
    frames_dir = os.path.join(out_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    # save the .blend for reproducibility (Design-0023 §[5]: brief + .blend kept)
    blend_path = os.path.join(out_dir, "scene.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    _log(f"saved reproducible scene -> {blend_path}")

    # [5] render the RAW (view-transformed, display-referred) frame sequence.
    # NB: the painterly post-grade runs as a NUMPY post-pass over these PNGs, NOT in the
    # scene compositor. On this Blender 5.1.2 flatpak the scene-compositor output skips the
    # view transform, which clips even a passthrough graph to white (verified by bisect).
    # Grading the already-display-referred PNG sidesteps that color-management trap and is
    # the path proven to work on this box. (painterly.apply() — the compositor route — is
    # kept for builds where the compositor color-manages correctly.)
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = os.path.join(frames_dir, "f_")
    _log("rendering RAW frame sequence (EEVEE)...")
    bpy.ops.render.render(animation=True)

    # the painterly numpy grade -> graded/
    graded_dir = os.path.join(out_dir, "graded")
    final_dir = frames_dir
    if not no_grade:
        os.makedirs(graded_dir, exist_ok=True)
        _log("applying painterly numpy post-grade frame-by-frame...")
        for f in range(scene.frame_start, scene.frame_end + 1):
            src = os.path.join(frames_dir, f"f_{f:04d}.png")
            dst = os.path.join(graded_dir, f"g_{f:04d}.png")
            if os.path.exists(src):
                painterly.grade_frame_numpy(src, dst, brief, mark_failed=validator_failed)
        final_dir = graded_dir
    elif validator_failed:
        # --no-grade path: still stamp the marker IN PLACE on the raw frames so a
        # failed gate is never silent regardless of the grade flag.
        _log("stamping VALIDATOR-FAILED marker onto RAW frames (--no-grade)...")
        for f in range(scene.frame_start, scene.frame_end + 1):
            p = os.path.join(frames_dir, f"f_{f:04d}.png")
            if os.path.exists(p):
                painterly.grade_frame_numpy(p, p, {"render": {"style": "none"}},
                                            mark_failed=True)

    # encode an mp4 from the FINAL frames via the system ffmpeg (this Blender flatpak
    # has no FFMPEG output enum; run.sh does the encode after Blender exits). We just
    # record the chosen frame dir + pattern for run.sh to pick up.
    with open(os.path.join(out_dir, "render.json"), "w") as jf:
        json.dump({
            "frames_dir": frames_dir,
            "graded_dir": graded_dir if not no_grade else None,
            "final_dir": final_dir,
            "final_pattern": ("g_%04d.png" if not no_grade else "f_%04d.png"),
            "fps": fps, "frame_start": scene.frame_start, "frame_end": scene.frame_end,
            "res": [res_x, res_y], "valid": path_result["valid"],
            "degraded": degraded, "validator_failed": validator_failed,
            "report": path_result["report"],
        }, jf, indent=2)

    prefix = "g" if not no_grade else "f"
    first_frame = os.path.join(final_dir, f"{prefix}_{scene.frame_start:04d}.png")
    _log(f"DONE. raw frames: {frames_dir}/  graded: "
         f"{graded_dir if not no_grade else '(none)'}/")
    _log(f"first final frame: {first_frame}")
    _log("lucid seam (Design-0023 §[5], lucid_engine.py:269):")
    _log(f'  python3 apps/dreaming/lucid/lucid_engine.py start amber --image "{first_frame}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
