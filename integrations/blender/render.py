# render.py — FIXED, repo-owned Blender render script (ADR-0022 §7).
#
# This is NEVER an agent parameter. The `blender-render` Spawn profile (lease.rs PROFILES) runs
# `blender --factory-startup --disable-autoexec -b [scratch.blend] --python render.py`, and THIS
# script is the only Python that reaches Blender. The agent/caller supplies only validated scalars
# (output path, samples, device, resolution, stress) via env vars set by render-wrapper.sh — never
# code, never a path-to-execute. `--disable-autoexec` also stops a malicious .blend from auto-running
# embedded Python. That is the "code disposes by construction" boundary: there is no code surface.
#
# Config (env, set by the wrapper from validated scalars):
#   AOS_BLENDER_OUT      absolute output PNG path (required)
#   AOS_BLENDER_SAMPLES  Cycles samples            (default 64)
#   AOS_BLENDER_DEVICE   OPTIX | CUDA | CPU         (default OPTIX, falls back to CPU if absent)
#   AOS_BLENDER_RES      square resolution px       (default 512)
#   AOS_BLENDER_STRESS   0 = normal; >0 deliberately inflates VRAM for the OOM acceptance test
#
# With --factory-startup and no input .blend, Blender opens the default scene (cube + camera + light),
# so this is a self-contained smoke test needing zero external assets.
#
# Cycles has NO hard VRAM ceiling API — the levers are device choice, tile size, texture limit, and
# scene size. So a pathological scene CAN still OOM the render; that is exactly what the Phase-0/1
# deliberate-OOM acceptance test (AOS_BLENDER_STRESS) verifies the substrate survives. On OOM Blender
# aborts non-zero, render-wrapper.sh propagates that, agentosd reaps the owned PID and reclaims VRAM.

import os
import sys

import bpy


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _log(msg: str) -> None:
    # stderr so it lands in the agentosd-owned job's log, not stdout (which Blender uses heavily).
    print(f"[blender-render] {msg}", file=sys.stderr, flush=True)


def _select_device(requested: str) -> str:
    """Pick the Cycles device explicitly — headless does NOT reliably auto-select a GPU
    (devtalk #12176). Returns the device class actually used ('GPU' or 'CPU')."""
    if requested == "CPU":
        bpy.context.scene.cycles.device = "CPU"
        return "CPU"

    prefs = bpy.context.preferences.addons["cycles"].preferences
    # Try the requested backend first, then the other GPU backend, before giving up to CPU.
    for backend in (requested, "OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = backend
        except TypeError:
            continue  # this Blender build lacks that backend enum
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type == backend]
        if gpus:
            for d in prefs.devices:
                d.use = d.type == backend
            bpy.context.scene.cycles.device = "GPU"
            _log(f"using GPU backend {backend}: {', '.join(d.name for d in gpus)}")
            return "GPU"

    _log("no usable GPU device found — falling back to CPU (smoke test still produces a frame)")
    bpy.context.scene.cycles.device = "CPU"
    return "CPU"


def _inflate_for_oom(stress: int) -> None:
    """Deliberately consume VRAM for the acceptance test. Dense subdivision → a huge BVH that must
    fit in VRAM. Tune AOS_BLENDER_STRESS upward until it OOMs on the target card; the test then
    confirms the desktop survives + the lease auto-releases + VRAM is reclaimed (ADR-0022 §Phase-0)."""
    _log(f"STRESS={stress}: inflating scene to pressure VRAM (this is the OOM acceptance test)")
    cube = bpy.data.objects.get("Cube")
    if cube is None:
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
    mod = cube.modifiers.new(name="oom_subsurf", type="SUBSURF")
    mod.subdivision_type = "SIMPLE"
    # Each render level +1 roughly quadruples faces; 6 + stress climbs fast. Cap to avoid a
    # multi-hour CPU fallback if no GPU is present.
    mod.render_levels = min(6 + stress, 11)


def main() -> int:
    out = os.environ.get("AOS_BLENDER_OUT")
    if not out:
        _log("AOS_BLENDER_OUT not set — refusing to render to an unknown path")
        return 2

    samples = _env_int("AOS_BLENDER_SAMPLES", 64)
    res = _env_int("AOS_BLENDER_RES", 512)
    stress = _env_int("AOS_BLENDER_STRESS", 0)
    device = os.environ.get("AOS_BLENDER_DEVICE", "OPTIX").upper()

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    used = _select_device(device)

    scene.cycles.samples = samples
    # Conservative VRAM levers (not a hard cap — see header): small tiles + a texture-size limit.
    scene.cycles.tile_size = 256
    scene.cycles.texture_limit = "2048"
    scene.cycles.use_denoising = True

    scene.render.resolution_x = res
    scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = out

    if stress > 0:
        _inflate_for_oom(stress)

    _log(f"rendering {res}x{res} @ {samples} spp on {used} → {out}")
    bpy.ops.render.render(write_still=True)

    if not os.path.exists(out):
        _log(f"render reported success but {out} is missing")
        return 1
    _log(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
