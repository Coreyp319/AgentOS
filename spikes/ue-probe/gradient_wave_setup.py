# gradient_wave_setup.py — author the "Abyssal" gradient-wave wallpaper tableau.
#
# WHAT THIS IS (ADR-0023, the first REAL dark-ride tableau — replaces the
# cube/sphere measurement scaffold in scene_setup.py)
#   A calm, flowing, soft-gradient WAVE "worthy of Apple": a single large flat
#   plane wearing an UNLIT emissive gradient material — a dark, cool, single-hue
#   color field (abyss-indigo -> slate -> one dim cool accent) that flows via
#   three SUMMED INCOMMENSURATE sines (so it never visibly loops), seen as a
#   luminous misty plain receding to a dark foggy horizon with calm negative
#   space above. No geometry displacement (WPO deferred). Self-lit, so it is
#   floor-rung-invariant: Lumen GI/Reflections can be OFF with zero visual loss.
#
#   This is the design-council "Abyssal" direction (2026-06-19):
#     - art-director + visual-systems-designer: cool dark single-hue journey,
#       warmth RESERVED as a signal (the field is NEVER warm; warm = needs-you
#       only). OkLCh-locked stops given here as LINEAR-RGB (UE is linear space).
#     - motion-designer + generative-artist: 3 summed sines at 22.0/14.3/8.7 s,
#       amplitudes 1.0/0.55/0.28, sub-threshold ("have to watch to see it move"),
#       single domain-warp, no FBM. The decimals are LOAD-BEARING — see below.
#     - design-technologist + rater-{craft,feasibility}: all-Python material via
#       unreal.MaterialEditingLibrary, UNLIT/emissive, every wire ASSERTED, a
#       recompile error ABORTS (no black material may reach the success marker).
#
# HOW TO RUN IT (headless, same harness as scene_setup.py)
#   SCENE_SCRIPT=gradient_wave_setup.py MARK='Abyssal gradient-wave scene built' \
#     bash spikes/ue-probe/author_scene.sh
#   (author_scene.sh adds the VRAM pre-flight gate + watchdog. *** NO inner quotes
#   around the -ExecCmds path *** — UE re-quotes the value itself; inner quotes
#   collide and the editor idles forever. The path has no spaces, so none needed.)
#
#   It authors INTO /Game/AgentOS/CalmWallpaper (the SAME map the whole measure/
#   coexist/frametime harness already points at — GameDefaultMap + MapsToCook are
#   set for it), replacing the scaffold's contents. scene_setup.py is kept in git
#   so the primitive baseline is still re-authorable.
#
# API NOTES (UE 5.8 Python, verified by design-technologist vs the 5.8 docs;
# NOT yet executed on this install — the assert-every-wire discipline below is
# precisely to catch the one unverified seam: input-pin string names):
#   AssetTools.create_asset(name, pkg, unreal.Material, unreal.MaterialFactoryNew())
#   MaterialEditingLibrary.create_material_expression(mat, ExprClass, x, y)
#   MaterialEditingLibrary.connect_material_expressions(a, "a_out", b, "b_in")->bool
#   MaterialEditingLibrary.connect_material_property(a, "a_out", MP_EMISSIVE_COLOR)
#   MaterialEditingLibrary.recompile_material(mat)   # log + treat errors as fatal
#   EditorAssetLibrary.{does_asset_exist,delete_asset,save_asset}
#   VectorParameter default = `constant` (LinearColor); ScalarParameter = `default_value`.

import os

import unreal

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
AGENTOS_PREFIX = "AgentOS_"                       # actor-label idempotency tag
LEVEL_PATH = "/Game/AgentOS/CalmWallpaper"        # SAME map the harness measures
MAT_DIR    = "/Game/AgentOS/Materials"
MAT_NAME   = "M_AgentOS_Abyssal"                  # AgentOS_-namespaced for cleanup
MAT_PATH   = MAT_DIR + "/" + MAT_NAME

PLANE_ASSET = "/Engine/BasicShapes/Plane.Plane"   # 2 tris is plenty for a
                                                  # fragment-only emissive field.

# --- Palette: "Abyssal" (visual-systems-designer, OkLCh-locked, LINEAR-RGB) ----
# Cool dark single-hue journey. These are LINEAR values (srgb_to_linear of the
# hex), pasted straight into FLinearColor. WARM IS RESERVED — no stop may carry
# chroma in the 35-45 deg dawn band; that hue belongs to the needs-you signal.
COL_ABYSS  = unreal.LinearColor(0.0017, 0.0022, 0.0048, 1.0)  # #070910 void
COL_MID    = unreal.LinearColor(0.0109, 0.0241, 0.0497, 1.0)  # #1B2A3D anchor
COL_LIFT   = unreal.LinearColor(0.0356, 0.0844, 0.1518, 1.0)  # #33506A depth
# accent (crest) — preview two hue families: cool blue-white (restrained) vs
# aurora-teal (more distinctive/alive). Pick via ABYSSAL_ACCENT=blue|teal.
_ACCENTS = {
    "blue": unreal.LinearColor(0.2271, 0.3855, 0.5520, 1.0),  # #7FA6C4 cool blue-white
    "teal": unreal.LinearColor(0.0780, 0.5600, 0.4600, 1.0),  # #4FC7B8 aurora-teal
}
COL_ACCENT = _ACCENTS.get(os.environ.get("ABYSSAL_ACCENT", "blue"), _ACCENTS["blue"])

# Gradient stops as (position 0..1 along the depth axis, color). near=1 (bright
# crest), far=0 (abyss). A chained-lerp piecewise ramp (correct multi-stop):
#   result = c0; for each seg: result = lerp(result, c_next, saturate((g-p0)/dp))
RAMP_STOPS = [
    (0.00, COL_ABYSS),
    (0.45, COL_MID),     # anchor low so the long dark run does not grey-sag/band
    (0.78, COL_LIFT),
    (1.00, COL_ACCENT),  # bright accent concentrated near the crest (small area)
]

# --- Motion: 3 SUMMED INCOMMENSURATE sine flows (motion-designer) --------------
# period = real seconds for one temporal cycle; spatial = cycles across the
# lateral axis; amp = UV-space displacement of the brightness band.
#   *** THE DECIMALS ARE LOAD-BEARING — DO NOT ROUND. ***
# 22.0/14.3/8.7 share no small common multiple, so the true repeat is on the
# order of hours; round them to 22/14/9 and it tiles visibly within a minute.
WAVE_LAYERS = [
    # (period_s, spatial_freq, amplitude)
    (22.0, 1.3, 0.040),   # A — dominant slow swell
    (14.3, 2.1, 0.022),   # B — cross flow, breaks the single-axis read
    (8.7,  3.4, 0.011),   # C — fine shimmer
]
# master slow-mo knob; reduce-motion clamps this -> 0. Real calm look = 1.0 (sub-
# threshold). Bump via ABYSSAL_SPEED for a motion-capture demo so the slow wave is
# legible in a short clip (labelled as sped-up; the shipped value stays 1.0).
GLOBAL_SPEED_DEFAULT = float(os.environ.get("ABYSSAL_SPEED", "1.0"))
EMISSIVE_SCALE_DEFAULT = 1.0 # global brightness; keep accent bloom a whisper

# --- DIAGNOSTIC ----------------------------------------------------------------
# When True, bypass the gradient/wave graph and emit a FLAT BRIGHT color. Used to
# bisect a black-preview: if the flat plane shows, framing/exposure/assignment are
# fine and the gradient orientation is the bug; if still black, it is framing or
# material assignment. Set back to False for the real look.
DEBUG_FLAT_EMISSIVE = False
DEBUG_FLAT_COLOR = unreal.LinearColor(0.60, 0.15, 0.45, 1.0)  # unmistakable magenta

# Exposure: an unlit emissive scene needs a FIXED manual exposure — manual bias 0
# renders the dim palette black, auto-exposure washes it out AND "breathes" as the
# wave moves (fatal for a calm wallpaper). We pin AEM_MANUAL with a tuned bias.
# Bracket it without re-editing: ABYSSAL_EXP_BIAS=<stops>, or ABYSSAL_AUTO_EXP=1
# to fall back to auto-exposure for debugging.
EXP_BIAS = float(os.environ.get("ABYSSAL_EXP_BIAS", "10.0"))
EXP_AUTO = os.environ.get("ABYSSAL_AUTO_EXP", "0") == "1"
# Fog density — for the full-frame field comp we don't want a horizon wash; off by
# default (the field carries its own depth via the vignette). Tunable for the look.
FOG_DENSITY = float(os.environ.get("ABYSSAL_FOG", "0.0"))

# --- The art: a flowing multi-hue aurora colour field, authored as HLSL ---------
# One Custom-node shader (the aurora.frag lineage) instead of a 30-node graph:
# expressive, cheap (~unlit, a few dozen ALU), and it dodges pin-name fragility.
# Inputs: UV (float2, 0..1 across the surface) and Time (float, already * speed).
# Returns float3 linear emissive colour. Cool dark-calm palette with drifting
# indigo->violet->teal->cyan ribbons + a gentle vignette for framing/glow.
HLSL_FIELD = """
float t = Time;

// Depth-of-field, analytic: blur radius grows from 0 at the centre to soft at the
// edges. We supersample the field (incl. its fine shimmer) N times within that
// radius -> crisp centre, creamy out-of-focus edges (the high-end-ad read). Real
// DoF needs fine detail to throw out of focus, so the field carries a shimmer.
float r = length(UV - 0.5);
float blur = smoothstep(0.14, 0.62, r) * 0.055;
const float2 offs[9] = { float2(0,0), float2(1,0), float2(-1,0), float2(0,1),
                         float2(0,-1), float2(0.7,0.7), float2(-0.7,0.7),
                         float2(0.7,-0.7), float2(-0.7,-0.7) };
int N = (blur < 0.0012) ? 1 : 9;

float3 acc = 0;
[loop] for (int i = 0; i < N; i++)
{
    float2 uv = UV + offs[i] * blur;
    float2 p = (uv - 0.5) * float2(2.4, 1.5);

    // single-level domain warp -> organic flow (not a rolling sine)
    float2 wv;
    wv.x = sin(p.y*1.7 + t*0.18) + 0.5*sin(p.x*1.3 - t*0.13);
    wv.y = sin(p.x*1.9 - t*0.15) + 0.5*sin(p.y*1.5 + t*0.10);
    p += 0.22 * wv;

    // flowing iso-field: three incommensurate drifting waves (no visible loop)
    float f = 0.50*sin(p.x*1.30 + p.y*0.70 + t*0.16)
            + 0.30*sin(p.x*2.10 - p.y*1.60 - t*0.11)
            + 0.20*sin(p.y*2.70 + p.x*0.30 + t*0.07);
    f = saturate(f*0.5 + 0.5);

    // slower second field drifts the hue independently of the structure
    float hue = saturate(0.5 + 0.5*sin(p.x*0.80 - p.y*0.90 + t*0.05));

    // cool aurora palette (linear): abyss -> indigo -> violet -> teal -> cyan
    float3 cAbyss  = float3(0.004, 0.006, 0.022);
    float3 cIndigo = float3(0.030, 0.050, 0.220);
    float3 cViolet = float3(0.140, 0.060, 0.300);
    float3 cTeal   = float3(0.030, 0.300, 0.380);
    float3 cCyan   = float3(0.250, 0.620, 0.700);
    float3 col = lerp(cAbyss,  cIndigo, smoothstep(0.00, 0.45, f));
    col = lerp(col, cViolet, smoothstep(0.30, 0.65, f) * hue);
    col = lerp(col, cTeal,   smoothstep(0.55, 0.85, f));
    col = lerp(col, cCyan,   smoothstep(0.85, 1.00, f) * 0.85);

    // fine luminous shimmer -> crisp in the focal centre, blurs to soft glints
    // at the edges (this is what makes the DoF read on a smooth field).
    float2 q = (uv - 0.5) * float2(9.0, 7.0) + 0.25 * wv;
    float spark = pow(saturate(sin(q.x*1.7 + t*0.30) * sin(q.y*1.9 - t*0.22)), 7.0);
    col += spark * float3(0.30, 0.62, 0.74) * 0.30;

    acc += col;
}
float3 outc = acc / N;

// gentle vignette: luminous centre, calm dark edges (keeps the restraint)
float vig = smoothstep(1.32, 0.28, length((UV-0.5)*float2(1.15,1.0)));
outc *= lerp(0.35, 1.0, vig);
return outc;
"""

# ---------------------------------------------------------------------------
# Subsystems / helpers
# ---------------------------------------------------------------------------
_actor_subsys = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
_level_subsys = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
_assets       = unreal.AssetToolsHelpers.get_asset_tools()
MEL           = unreal.MaterialEditingLibrary

# A build-failure accumulator. ANY failed wire / non-empty recompile makes
# main() raise at the end, so author_scene.sh's watchdog sees a non-marker exit
# and reports FAIL (rater-craft delta #1: a black material must never PASS).
_FAIL = []


def log(msg):
    unreal.log("[AgentOS gradient_wave] " + str(msg))


def _label(actor, name):
    actor.set_actor_label(AGENTOS_PREFIX + name)
    return actor


def _try_set(obj, prop, value):
    """Tolerant set_editor_property for COSMETIC props (exposure/bloom/fog): a
    renamed property in this UE point-release logs + skips rather than aborting
    the whole build before the level saves. Tunable visuals, not load-bearing."""
    try:
        obj.set_editor_property(prop, value)
        return True
    except Exception as exc:  # noqa: BLE001
        log("skip cosmetic set {}={!r}: {}".format(prop, value, exc))
        return False


def _try_set_first(obj, props, value):
    """Try several candidate property names (API drift across 5.x), use the first
    that sticks. Returns the name that worked, or None."""
    for p in props:
        if _try_set(obj, p, value):
            return p
    return None


def clear_prior():
    """Destroy any actor this script previously created (idempotency)."""
    removed = 0
    for actor in _actor_subsys.get_all_level_actors():
        try:
            if actor.get_actor_label().startswith(AGENTOS_PREFIX):
                _actor_subsys.destroy_actor(actor)
                removed += 1
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            log("skip actor during clear: {}".format(exc))
    log("cleared {} prior AgentOS actor(s)".format(removed))


# ---- material-graph helpers (every wire asserted) -------------------------
def _expr(mat, cls, x, y):
    return MEL.create_material_expression(mat, cls, x, y)


def _wire(a, a_out, b, b_in):
    """connect_material_expressions, asserting the bool return.

    A wrong/case-mismatched input-pin NAME returns False and silently leaves the
    input unconnected — the material still recompiles 'clean' using the node's
    const fallback, so you ship a flat color, not a gradient. We record every
    failure so the build aborts loudly instead. (rater-{craft,feasibility} #1.)
    """
    ok = MEL.connect_material_expressions(a, a_out, b, b_in)
    if not ok:
        msg = "WIRE FAILED: {}.{!r} -> {}.{!r}".format(
            a.get_class().get_name(), a_out, b.get_class().get_name(), b_in)
        log(msg)
        _FAIL.append(msg)
    return b


def _wire_prop(a, a_out, prop):
    ok = MEL.connect_material_property(a, a_out, prop)
    if not ok:
        msg = "PROP WIRE FAILED: {}.{!r} -> {}".format(
            a.get_class().get_name(), a_out, prop)
        log(msg)
        _FAIL.append(msg)


def _const(mat, value, x, y):
    n = _expr(mat, unreal.MaterialExpressionConstant, x, y)
    n.set_editor_property("r", float(value))
    return n


def _mul(mat, a, b, x, y, a_out="", b_in="A"):
    """a * b  (a is an expression on input A; b on input B)."""
    n = _expr(mat, unreal.MaterialExpressionMultiply, x, y)
    _wire(a, a_out, n, "A")
    _wire(b, "", n, "B")
    return n


def _mul_const(mat, a, k, x, y, a_out=""):
    """a * constant — uses the Multiply node's const_b so we need no extra node."""
    n = _expr(mat, unreal.MaterialExpressionMultiply, x, y)
    n.set_editor_property("const_b", float(k))
    _wire(a, a_out, n, "A")
    return n


def _add(mat, a, b, x, y, a_out="", b_out=""):
    n = _expr(mat, unreal.MaterialExpressionAdd, x, y)
    _wire(a, a_out, n, "A")
    _wire(b, b_out, n, "B")
    return n


def _sub_const(mat, a, k, x, y, a_out=""):
    """a - constant via Add with const_b = -k."""
    n = _expr(mat, unreal.MaterialExpressionAdd, x, y)
    n.set_editor_property("const_b", float(-k))
    _wire(a, a_out, n, "A")
    return n


def _saturate(mat, a, x, y, a_out=""):
    n = _expr(mat, unreal.MaterialExpressionClamp, x, y)
    n.set_editor_property("min_default", 0.0)
    n.set_editor_property("max_default", 1.0)
    _wire(a, a_out, n, "")
    return n


def _sine(mat, a, x, y, a_out=""):
    # UE Sine: out = sin(2*pi * in / Period); Period default 1.0 -> sin(2*pi*in).
    n = _expr(mat, unreal.MaterialExpressionSine, x, y)
    _wire(a, a_out, n, "")
    return n


def _vec_param(mat, name, color, x, y):
    n = _expr(mat, unreal.MaterialExpressionVectorParameter, x, y)
    n.set_editor_property("parameter_name", name)
    # UE 5.8: VectorParameter's default is `default_value` (LinearColor) — NOT
    # `constant` (that's Constant3Vector; the two are easy to swap and the wrong
    # name raises). Try in order so an API surprise logs instead of aborting.
    last = None
    for prop in ("default_value", "constant"):
        try:
            n.set_editor_property(prop, color)
            return n
        except Exception as exc:  # noqa: BLE001
            last = exc
    _FAIL.append("VectorParameter {!r}: no settable default prop ({})".format(name, last))
    return n


def _scalar_param(mat, name, default, x, y):
    n = _expr(mat, unreal.MaterialExpressionScalarParameter, x, y)
    n.set_editor_property("parameter_name", name)
    n.set_editor_property("default_value", float(default))  # Scalar = default_value
    return n


# ---------------------------------------------------------------------------
# The material
# ---------------------------------------------------------------------------
def build_material():
    """Create /Game/AgentOS/Materials/M_AgentOS_Abyssal — unlit emissive gradient
    wave. Returns the unreal.Material, or None on a hard failure."""
    # Idempotency: a stale asset would make create_asset collide (and a -unattended
    # editor would hang on the overwrite prompt). Delete first. The plane that
    # referenced it is already gone (clear_prior ran), so the delete is unblocked.
    if unreal.EditorAssetLibrary.does_asset_exist(MAT_PATH):
        unreal.EditorAssetLibrary.delete_asset(MAT_PATH)
        log("deleted prior material {}".format(MAT_PATH))

    mat = _assets.create_asset(MAT_NAME, MAT_DIR, unreal.Material,
                               unreal.MaterialFactoryNew())
    if mat is None:
        _FAIL.append("create_asset returned None for {}".format(MAT_PATH))
        return None

    # UNLIT is the ONLY "is unlit" switch — forget it and the gradient gets lit,
    # shadowed, and GI-tinted (and pays for it). Self-lit emissive = floor-rung
    # invariant (looks identical with Lumen off).
    mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_OPAQUE)
    mat.set_editor_property("two_sided", True)   # full-frame wall: visible either facing

    if DEBUG_FLAT_EMISSIVE:
        flat = _expr(mat, unreal.MaterialExpressionConstant3Vector, -300, 0)
        flat.set_editor_property("constant", DEBUG_FLAT_COLOR)
        _wire_prop(flat, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
        MEL.layout_material_expressions(mat)
        MEL.recompile_material(mat)
        unreal.EditorAssetLibrary.save_asset(MAT_PATH)
        log("DEBUG flat emissive material built + saved: {}".format(MAT_PATH))
        return mat

    # --- coordinate + time -----------------------------------------------------
    uv = _expr(mat, unreal.MaterialExpressionTextureCoordinate, -900, 0)
    gspeed = _scalar_param(mat, "GlobalSpeed", GLOBAL_SPEED_DEFAULT, -900, 320)
    time_n = _expr(mat, unreal.MaterialExpressionTime, -900, 200)
    # one knob re-times everything; reduce-motion / throttle drives it -> 0 to
    # FREEZE the field (the reserved reactivity seam the coordinator pokes).
    t = _mul(mat, time_n, gspeed, -680, 260)

    # --- the art: the flowing aurora field in ONE Custom HLSL node -------------
    custom = _expr(mat, unreal.MaterialExpressionCustom, -380, 0)
    _try_set(custom, "output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    _try_set(custom, "description", "AbyssalAuroraField")
    ci_uv = unreal.CustomInput()
    _try_set(ci_uv, "input_name", "UV")
    ci_t = unreal.CustomInput()
    _try_set(ci_t, "input_name", "Time")
    _try_set(custom, "inputs", [ci_uv, ci_t])
    _try_set(custom, "code", HLSL_FIELD)
    _wire(uv, "", custom, "UV")
    _wire(t, "", custom, "Time")

    escale = _scalar_param(mat, "EmissiveScale", EMISSIVE_SCALE_DEFAULT, 120, 220)
    emissive = _mul(mat, custom, escale, 320, 0)
    _wire_prop(emissive, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

    # --- compile (wire failures already accumulate into _FAIL) + save ----------
    MEL.layout_material_expressions(mat)
    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(MAT_PATH)
    log("material (aurora field) built + saved: {}".format(MAT_PATH))
    return mat


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------
def build_field_plane(mat):
    """A large plane standing as a WALL facing the camera, wearing the flowing
    aurora field so the colour fills the whole frame (full-bleed, the Apple-
    gradient read) instead of compressing to a horizon line."""
    mesh = unreal.EditorAssetLibrary.load_asset(PLANE_ASSET)
    actor = _actor_subsys.spawn_actor_from_class(
        unreal.StaticMeshActor, unreal.Vector(0.0, 0.0, 0.0),
        unreal.Rotator(0.0, 90.0, 0.0))            # stand the plane up (vertical wall)
    smc = actor.static_mesh_component
    smc.set_static_mesh(mesh)
    actor.set_actor_scale3d(unreal.Vector(70.0, 70.0, 1.0))  # ~70 m wall ~= frame at the cam dist
    if mat is not None:
        smc.set_material(0, mat)
    _label(actor, "FieldPlane")


def build_post_and_fog():
    """Manual-exposure post (blacks stay black, palette renders 1:1) + cool
    height fog for the foggy-horizon depth and the calm negative space above."""
    ppv = _actor_subsys.spawn_actor_from_class(
        unreal.PostProcessVolume, unreal.Vector(0.0, 0.0, 0.0))
    ppv.set_editor_property("unbound", True)
    s = ppv.get_editor_property("settings")
    # Fixed manual exposure tuned to show the dim Abyssal palette (auto washes +
    # breathes; manual-0 is black). Bracket via ABYSSAL_EXP_BIAS env.
    if EXP_AUTO:
        log("exposure: AUTO (debug)")
    else:
        _try_set(s, "override_auto_exposure_method", True)
        _try_set(s, "auto_exposure_method", unreal.AutoExposureMethod.AEM_MANUAL)
        _try_set(s, "override_auto_exposure_bias", True)
        _try_set(s, "auto_exposure_bias", EXP_BIAS)
        log("exposure: MANUAL bias={}".format(EXP_BIAS))
    # Bloom: lifted for the high-end-ad glow on the shimmer/crests (still measured,
    # not a bloom-bomb). Tunable via ABYSSAL_BLOOM.
    _try_set(s, "override_bloom_intensity", True)
    _try_set(s, "bloom_intensity", float(os.environ.get("ABYSSAL_BLOOM", "0.70")))
    # Film grain: subtle, doubles as anti-banding on the near-flat dark gradient
    # (visual-systems-designer: ~1.5-2% to kill 8-bit contour rings).
    _try_set(s, "override_film_grain_intensity", True)
    _try_set(s, "film_grain_intensity", 0.18)
    # NOTE: deliberately NOT overriding Lumen GI/Reflections here — the surface is
    # unlit, so GI contributes nothing; leaving it to the cvar ladder keeps this
    # tableau floor-rung invariant and removes a VRAM confounder.
    ppv.set_editor_property("settings", s)
    _label(ppv, "PostFX")

    fog = _actor_subsys.spawn_actor_from_class(
        unreal.ExponentialHeightFog, unreal.Vector(0.0, 0.0, 0.0))
    fc = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
    if fc:
        _try_set(fc, "fog_density", FOG_DENSITY)
        _try_set(fc, "fog_height_falloff", 0.18)
        # cool, dark inscattering so the horizon + negative space read as cold
        # night, not muddy grey. Property renamed across 5.x (color -> luminance).
        _try_set_first(
            fc, ("fog_inscattering_luminance", "fog_inscattering_color", "inscattering_color"),
            unreal.LinearColor(0.012, 0.020, 0.040, 1.0))
    _label(fog, "HeightFog")


def build_camera():
    """Head-on camera facing the wall so the flowing aurora field fills the frame.

    A plain CameraActor (NOT CineCamera) on purpose: a CineCamera applies physical
    exposure (ISO/aperture/shutter) that crushes this dim unlit scene to black in
    a `-game`/packaged run — the manual PostProcessVolume exposure must be the only
    exposure.
    """
    cam = _actor_subsys.spawn_actor_from_class(
        unreal.CameraActor,
        unreal.Vector(-4200.0, 0.0, 0.0),   # straight in front of the wall (at x=0)
        unreal.Rotator(0.0, 0.0, 0.0))       # look +X, level
    cc = cam.get_component_by_class(unreal.CameraComponent)
    if cc:
        _try_set(cc, "field_of_view", 75.0)   # a touch tighter so the field fills, edges off
    # Auto-activate as the Player0 view so a `-game`/packaged standalone run
    # renders from THIS camera (not a default pawn at the origin) — needed for
    # the live windowed/wallpaper run, not just the editor-viewport preview.
    _try_set(cam, "auto_activate_for_player", unreal.AutoReceiveInput.PLAYER0)
    _label(cam, "Camera")
    return cam


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    if unreal.EditorAssetLibrary.does_asset_exist(LEVEL_PATH):
        _level_subsys.load_level(LEVEL_PATH)
        log("loaded existing level {}".format(LEVEL_PATH))
    else:
        created = _level_subsys.new_level(LEVEL_PATH)
        log("new_level({}) -> {}".format(LEVEL_PATH, created))

    clear_prior()                      # destroy old actors BEFORE deleting the mat
    mat = build_material()             # accumulates into _FAIL on any wire/compile
    build_field_plane(mat)
    build_post_and_fog()
    build_camera()

    saved = _level_subsys.save_current_level()
    log("save_current_level() -> {}".format(saved))

    if _FAIL:
        # A flat/black material must NEVER reach the success marker below
        # (rater-craft delta #1). We log loudly and RETURN (not raise) so the
        # editor still processes the trailing `Quit` ExecCmd instead of idling
        # to the watchdog — author_scene.sh keys FAIL off the missing marker.
        for f in _FAIL:
            unreal.log_error("[AgentOS gradient_wave] BUILD FAIL: " + f)
        log("BUILD FAILED with {} issue(s) — NOT emitting success marker".format(len(_FAIL)))
        return

    # The success marker author_scene.sh greps for (pass via MARK=...).
    log("Abyssal gradient-wave scene built at {}".format(LEVEL_PATH))


def _run():
    """Top-level guard: never let an uncaught exception leave the headless
    editor idling to the watchdog. Log the traceback, then fall through so the
    trailing `Quit` ExecCmd fires. FAIL is signalled by the absent marker."""
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        import traceback
        unreal.log_error("[AgentOS gradient_wave] FATAL: {}".format(exc))
        for line in traceback.format_exc().splitlines():
            unreal.log_error("[AgentOS gradient_wave] " + line)


# Runs under `__main__`, `py <path>`, and exec(open(...).read()) alike.
_run()
