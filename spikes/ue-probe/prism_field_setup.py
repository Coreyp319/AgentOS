# prism_field_setup.py — build the "Prism Field" high-key dispersive-glass wallpaper.
#
# WHAT THIS DOES (the UE 3-D sibling of the 2-D Aurora "Prism" shader, Style 15)
#   A HIGH-KEY studio tableau — the INVERSE of the dark Indigo Channel — that
#   reproduces the Apple "personalized listening / iridescent" reference
#   (/home/corey/Downloads/personalized_listening__geshsqt82yeu_large.jpg) with
#   REAL geometry that exhibits REAL view-dependent chromatic dispersion, not a
#   flat post-process. Composition (chosen 2026-06-24):
#     - a giant EMISSIVE WHITE BACKDROP plane (the high-key field; it also lights
#       the scene via Lumen GI — "where the white comes from")
#     - a dark abstract CENTRAL FORM (a tall tapered monolith) the coronas orbit
#     - overlapping glass CORONAS = thin disc meshes carrying a single Custom-HLSL
#       "prism" material that ports aurora.frag's prismCorona() math: a clean
#       centre, a bright iridescent rim, radiating streaks, and per-channel radial
#       sampling (r∓ca) → a rainbow dispersion FRINGE on every rim. The corona
#       "turns slowly" via a Time-driven phase IN THE MATERIAL (like the 2-D
#       shader's spin=0.05*t) — no fragile per-ring transform animation needed.
#     - a swirling RIBBON VORTEX (thin segments on a spiral) across the lower third
#     - a soft fill light + SkyLight, a MANUAL-exposure PPV with bloom (the rim
#       blooms — half the reference read), Lumen GI+Reflections pinned ON
#   Reactive: the prism material taps the existing MPC_AgentOS_Reactive scalars
#   (Motion → dispersion gain + streak-turn; Warm → needs-you dawn lift; Desat →
#   snag calm; ReduceMotion → freeze the turn). Idle deltas are 0 ⇒ byte-identical
#   to the static look (the ADR-0030 invariant). Dispersion technique = Recipe A
#   (Fresnel/refraction now; Substrate thin-film is the deferred upgrade).
#
# HOW TO RUN IT — under the VRAM-gated watchdog (the only safe headless path):
#     cd ~/Documents/AgentOS
#     SCENE_SCRIPT=prism_field_setup.py MARK='Prism Field scene built' \
#       bash spikes/ue-probe/author_scene.sh
#   Tune the look with PRISM_* env knobs (see Config). *** DO NOT add inner quotes
#   around the -ExecCmds path *** (author_scene.sh builds argv correctly; bare path).
#
# KEY API FACTS (verified on this box — getting these wrong costs a full editor cycle):
#   - the node→node connect is PLURAL: MEL.connect_material_expressions(a,"o",b,"i")
#   - the node→root  connect is:        MEL.connect_material_property(node,"o",MP_*)
#   - Custom node: output_type=unreal.CustomMaterialOutputType.CMOT_FLOAT4;
#     inputs = a python list of unreal.CustomInput() each with input_name; the
#     `code` body must `return` a value of the declared type; reference inputs by
#     their input_name as plain variables.
#   - unreal.Color positional args are BGRA — we use LinearColor everywhere (RGBA),
#     so no BGRA trap here. Every cosmetic setter is wrapped in _try_set.

import os
import math

import unreal

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
AGENTOS_PREFIX = "AgentOS_"
LEVEL_PATH = "/Game/AgentOS/PrismField"          # NEW map — never clobbers CalmWallpaper/Indigo
MAT_DIR    = "/Game/AgentOS/Materials"
MPC_PATH   = "/Game/AgentOS/Materials/MPC_AgentOS_Reactive"  # the existing reactive MPC

PRISM_MAT_NAME    = "M_AgentOS_Prism"
PRISM_MAT_PATH    = MAT_DIR + "/" + PRISM_MAT_NAME
BACKDROP_MAT_NAME = "M_AgentOS_Backdrop"
BACKDROP_MAT_PATH = MAT_DIR + "/" + BACKDROP_MAT_NAME
SLAB_MAT_NAME     = "M_AgentOS_PrismSlab"
SLAB_MAT_PATH     = MAT_DIR + "/" + SLAB_MAT_NAME
RIBBON_MAT_NAME   = "M_AgentOS_Ribbon"
RIBBON_MAT_PATH   = MAT_DIR + "/" + RIBBON_MAT_NAME

CUBE_ASSET   = "/Engine/BasicShapes/Cube.Cube"
PLANE_ASSET  = "/Engine/BasicShapes/Plane.Plane"


def _envflag(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _envf(name, default):
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


# Look knobs (defaults = the intended landed look; tune live).
PRISM_REFRACT   = _envflag("PRISM_REFRACT", True)    # True: translucent glass + real refraction; False: cheap masked-emissive
PRISM_REACTIVE  = _envflag("PRISM_REACTIVE", True)   # tap the MPC for reactivity (idle delta = 0)
PRISM_EXP_BIAS  = _envf("PRISM_EXP_BIAS", 0.0)       # AEM_MANUAL bias — high-key sits near 0 (Indigo was -3)
PRISM_BACKDROP  = _envf("PRISM_BACKDROP", 1.8)       # emissive white level — high enough to read WHITE through ACES
PRISM_DISP      = _envf("PRISM_DISP", 0.05)          # base dispersion width (the r∓ca split, normalized)
PRISM_HUE_SPREAD= _envf("PRISM_HUE_SPREAD", 0.5)     # 0 = theme-tinted fringe, 1 = full ROYGBIV
PRISM_RIM_BRIGHT= _envf("PRISM_RIM_BRIGHT", 7.0)     # emissive gain → pushes the rim past the bloom threshold
PRISM_SPIN      = _envf("PRISM_SPIN", 0.06)          # corona turn rate (the shader's spin=0.05*t)
PRISM_IOR       = _envf("PRISM_IOR", 1.05)           # glass IOR — keep LOW (1.5 smears on overlap)
PRISM_GLASS_OP  = _envf("PRISM_GLASS_OP", 0.85)      # glass band opacity — occludes the white field so the ring reads
PRISM_RIBBON    = _envflag("PRISM_RIBBON", True)     # build the lower-third ribbon vortex
PRISM_BLOOM     = _envf("PRISM_BLOOM", 0.6)
PRISM_BLOOM_THRESH = _envf("PRISM_BLOOM_THRESH", 2.5)  # ABOVE the white field so only the bright rims bloom

# Theme tint of the fringe (Aurora cyan→violet family); LinearColor, RGBA, no BGRA trap.
TINT_A = unreal.LinearColor(0.25, 0.65, 0.98, 1.0)   # cyan
TINT_B = unreal.LinearColor(0.58, 0.32, 0.98, 1.0)   # violet

# ---------------------------------------------------------------------------
# Subsystems / helpers
# ---------------------------------------------------------------------------
_actor_subsys = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
_level_subsys = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
_assets       = unreal.AssetToolsHelpers.get_asset_tools()
MEL           = unreal.MaterialEditingLibrary

_FAIL = []   # any hard failure ⇒ main() skips the success MARK ⇒ author_scene.sh reports FAIL


def log(msg):
    unreal.log("[AgentOS prism_field] " + str(msg))


def _label(actor, name):
    actor.set_actor_label(AGENTOS_PREFIX + name)
    return actor


def _try_set(obj, prop, value):
    """Tolerant set_editor_property for COSMETIC/drift-prone props: a renamed
    property in this point-release logs + skips rather than aborting the build."""
    try:
        obj.set_editor_property(prop, value)
        return True
    except Exception as exc:  # noqa: BLE001
        log("skip set {}={!r}: {}".format(prop, value, exc))
        return False


def _try_set_first(obj, props, value):
    for p in props:
        if _try_set(obj, p, value):
            return p
    return None


# ---- material node helpers ------------------------------------------------
def _expr(mat, cls, x, y):
    return MEL.create_material_expression(mat, cls, x, y)


def _wire(a, a_out, b, b_in):
    """node→node connect (PLURAL api). Records a hard failure: a silent False
    leaves the input at 0, which looks exactly like 'authored but inert'."""
    ok = MEL.connect_material_expressions(a, a_out, b, b_in)
    if not ok:
        # CollectionParameter's scalar sometimes isn't on the "" output — retry alts.
        for alt in ("Result", "Output", "Value"):
            if MEL.connect_material_expressions(a, alt, b, b_in):
                return
        msg = "WIRE FAILED: {}.{!r} -> {}.{!r}".format(
            a.get_class().get_name(), a_out, b.get_class().get_name(), b_in)
        log(msg)
        _FAIL.append(msg)


def _wire_prop(a, a_out, prop):
    if not MEL.connect_material_property(a, a_out, prop):
        msg = "PROP WIRE FAILED: {}.{!r} -> {}".format(a.get_class().get_name(), a_out, prop)
        log(msg)
        _FAIL.append(msg)


def _scalar(mat, name, val, x, y):
    n = _expr(mat, unreal.MaterialExpressionScalarParameter, x, y)
    _try_set(n, "parameter_name", name)
    _try_set(n, "default_value", val)
    return n


def _const(mat, val, x, y):
    n = _expr(mat, unreal.MaterialExpressionConstant, x, y)
    _try_set(n, "r", float(val))
    return n


def _vec(mat, name, lc, x, y):
    n = _expr(mat, unreal.MaterialExpressionVectorParameter, x, y)
    _try_set(n, "parameter_name", name)
    _try_set(n, "default_value", lc)
    return n


def _mul(mat, a, b, x, y):
    n = _expr(mat, unreal.MaterialExpressionMultiply, x, y)
    _wire(a, "", n, "A"); _wire(b, "", n, "B")
    return n


def _add(mat, a, b, x, y):
    n = _expr(mat, unreal.MaterialExpressionAdd, x, y)
    _wire(a, "", n, "A"); _wire(b, "", n, "B")
    return n


def _reactive(mat, mpc, axis, x, y):
    """A reactive scalar: a CollectionParameter tap on the MPC if available + enabled,
    else a Constant 0 (so the material is identical to static / fail-open). The two-tier
    'static default + reactive delta(0 at idle)' keeps idle byte-identical."""
    if PRISM_REACTIVE and mpc is not None:
        n = _expr(mat, unreal.MaterialExpressionCollectionParameter, x, y)
        if _try_set(n, "collection", mpc):
            # parameter_name is the scalar's NAME; the API also keys by GUID in some
            # builds — set the name and let the alt-output retry in _wire handle it.
            _try_set(n, "parameter_name", axis)
            return n
    return _const(mat, 0.0, x, y)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
# Environment leftovers (sun/sky/fog/PPV from any prior scene authored into this
# slot) get swept so the high-key room is entirely ours.
_ENV_CLASSES = (
    unreal.SkyAtmosphere, unreal.SkyLight, unreal.DirectionalLight,
    unreal.ExponentialHeightFog, unreal.PostProcessVolume,
)


def _is_env_leftover(actor):
    if isinstance(actor, _ENV_CLASSES):
        return True
    nm = (actor.get_actor_label() + " " + actor.get_class().get_name()).lower()
    return any(h in nm for h in ("sky", "sun", "atmospher"))


def clear_prior():
    removed = 0
    for actor in _actor_subsys.get_all_level_actors():
        try:
            if actor.get_actor_label().startswith(AGENTOS_PREFIX) or _is_env_leftover(actor):
                _actor_subsys.destroy_actor(actor)
                removed += 1
        except Exception as exc:  # noqa: BLE001
            log("skip actor during clear: {}".format(exc))
    log("cleared {} actor(s) (ours + env leftovers)".format(removed))


# ---------------------------------------------------------------------------
# The reactive MPC (reference the existing one; create a minimal one only if absent)
# ---------------------------------------------------------------------------
_REACT_AXES = ("Motion", "Fog", "Backlight", "Warm", "Desat", "Air", "Fresh", "ReduceMotion")


def ensure_mpc():
    if not PRISM_REACTIVE:
        return None
    if unreal.EditorAssetLibrary.does_asset_exist(MPC_PATH):
        mpc = unreal.EditorAssetLibrary.load_asset(MPC_PATH)
        log("reusing existing MPC {}".format(MPC_PATH))
        return mpc
    # Create a minimal MPC so the taps resolve even on a fresh project.
    try:
        mpc = _assets.create_asset(
            "MPC_AgentOS_Reactive", MAT_DIR,
            unreal.MaterialParameterCollection, unreal.MaterialParameterCollectionFactoryNew())
    except Exception as exc:  # noqa: BLE001
        log("MPC create failed ({}) — reactivity falls back to static".format(exc))
        return None
    if mpc is None:
        return None
    scalars = []
    for ax in _REACT_AXES:
        sp = unreal.CollectionScalarParameter()
        _try_set(sp, "parameter_name", unreal.Name(ax))
        _try_set(sp, "default_value", 1.0 if ax in ("Motion", "Fog", "Backlight") else 0.0)
        scalars.append(sp)
    _try_set(mpc, "scalar_parameters", scalars)
    unreal.EditorAssetLibrary.save_asset(MPC_PATH)
    log("created minimal MPC {} ({} scalars)".format(MPC_PATH, len(scalars)))
    return mpc


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------
# The prism Custom-HLSL: ports aurora.frag's prismCorona (rim ring + radiating
# streaks) and the r∓ca per-channel chromatic dispersion. UV is the disc's 0..1
# coords; the corona sits at normalized radius Reach. Output FLOAT4 = (rim rgb, mask).
_PRISM_HLSL = r"""
// A SWEPT IRIDESCENT ARC read against a WHITE field — the reference's warped-vinyl
// swirls, NOT a flat dartboard ring. A soft glass body gently occludes the white into a
// contour; a few crisp NESTED grooves (record lines) FADE along the sweep (long-exposure
// streak); a DELICATE chromatic fringe rides the leading edges ONLY (thin r∓ca). The
// quad's own roll/scale place + size each arc; Phase slowly turns the sweep. Output
// float4 = (emissive rgb, opacity a). Inputs unchanged so the node wiring is reused.
float2 p = (UV - 0.5) * 2.0;             // -1..1 across the quad
float  r = length(p);
float  ang = atan2(p.y, p.x);

const float Reach   = 0.66;              // arc radius in normalized quad space
const float ArcHalf = 1.75;             // LONG angular HALF-span (~100°) → reads as a sweep, not a lobe

// Seamless angular window centred on the (slowly spun) sweep dir — cos() dodges the
// atan2 seam (the aurora-shader gotcha). Gentle fade (pow 0.5) so the arc stays LONG,
// streaking out at the ends → the long-exposure motion feel.
float c    = cos(ang - Phase * 0.5);
float win  = saturate((c - cos(ArcHalf)) / (1.0 - cos(ArcHalf)));
float along = pow(win, 0.5);
// ASYMMETRIC sweep: weight one angular half brighter so the arc reads as a one-directional
// streak (a spinning-disc sweep / comet), NOT a mirror-symmetric butterfly fan. The quad's
// roll rotates this bias too, so different rolls = different sweep directions.
float side  = sin(ang - Phase * 0.5);              // signed across the sweep
along *= (0.28 + 0.72 * saturate(0.5 + 0.85 * side));

float dr   = r - Reach;

// HERO = a set of crisp NESTED grooves (warped-record lines) across a thin band, each
// chromatically split (sampled per-channel at r∓ca) → fine ROYGBIV fringe on the edges,
// white where the channels coincide. Crisp lines (not a fat blob) read as swept glass.
float ca = max(Disp, 1e-4);
float lW = 0.0, lR = 0.0, lB = 0.0;
[unroll] for (int i = 0; i < 5; i++) {
    float rr = Reach - 0.11 + 0.055 * float(i);       // 5 lines across a ~0.22 band
    lW += exp(-(r      - rr) * (r      - rr) * 2400.0);
    lR += exp(-(r - ca - rr) * (r - ca - rr) * 2400.0);
    lB += exp(-(r + ca - rr) * (r + ca - rr) * 2400.0);
}
float  wcore  = min(lW, min(lR, lB));
float3 fringe = saturate(float3(lR, lW, lB) - wcore);
float3 themeCol  = lerp(TintA.rgb, TintB.rgb, saturate(0.5 + dr * 2.0));
float3 fringeCol = lerp(fringe * themeCol * 1.4, fringe * 1.1, saturate(Spread));

// a SUBTLE soft halo so the arc sits in light (a thin glaze, NOT the old fat blob)
float band = exp(-dr * dr * 24.0) * along;

float3 emis = ((lW * 1.0 + fringeCol) * along + band * 0.10) * max(RimBright, 0.0) * 0.5;

// reactive: Desat (snag → calmer), Warm (needs-you → dawn lift)
float luma = dot(emis, float3(0.299, 0.587, 0.114));
emis = lerp(emis, luma.xxx, saturate(Desat));
emis += saturate(Warm) * float3(0.10, 0.06, 0.025) * band;

// OPACITY: crisp lines occlude the white into thin contours; the glaze is a faint veil.
float opac = saturate(lW * 0.55 * along + band * 0.28);
return float4(emis, opac);
"""


def _custom4(mat, code, inputs, x, y, desc):
    """A MaterialExpressionCustom returning a float4. `inputs` = list of input names."""
    c = _expr(mat, unreal.MaterialExpressionCustom, x, y)
    _try_set(c, "output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT4)
    _try_set(c, "description", desc)
    ci = []
    for nm in inputs:
        inp = unreal.CustomInput()
        _try_set(inp, "input_name", unreal.Name(nm))
        ci.append(inp)
    _try_set(c, "inputs", ci)
    _try_set(c, "code", code)
    return c


def build_prism_material(mpc):
    """The hero glass-corona material: one Custom node (minimal wire surface) doing
    the corona + dispersion + streaks; translucent refraction (Recipe A) or cheap
    masked-emissive. Reactive scalars feed the dispersion + turn."""
    if unreal.EditorAssetLibrary.does_asset_exist(PRISM_MAT_PATH):
        unreal.EditorAssetLibrary.delete_asset(PRISM_MAT_PATH)
    mat = _assets.create_asset(PRISM_MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
    if mat is None:
        _FAIL.append("create_asset None for prism material")
        return None

    _try_set(mat, "two_sided", True)
    if PRISM_REFRACT:
        _try_set(mat, "blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
        _try_set(mat, "shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
        _try_set_first(mat, ("translucency_lighting_mode",),
                       unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)
    else:
        _try_set(mat, "blend_mode", unreal.BlendMode.BLEND_MASKED)
        _try_set(mat, "shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
        _try_set(mat, "opacity_mask_clip_value", 0.06)

    # --- inputs to the Custom node ---
    uv     = _expr(mat, unreal.MaterialExpressionTextureCoordinate, -1300, -120)
    tint_a = _vec(mat, "PrismTintA", TINT_A, -1300, 40)
    tint_b = _vec(mat, "PrismTintB", TINT_B, -1300, 140)
    spread = _scalar(mat, "HueSpread", PRISM_HUE_SPREAD, -1300, 240)
    rimbr  = _scalar(mat, "RimBright", PRISM_RIM_BRIGHT, -1300, 320)
    dispp  = _scalar(mat, "DispersionWidth", PRISM_DISP, -1300, 400)

    # reactive taps (Constant 0 when reactivity off → idle byte-identical)
    motion = _reactive(mat, mpc, "Motion", -1300, 500)
    warm   = _reactive(mat, mpc, "Warm",   -1300, 580)
    desat  = _reactive(mat, mpc, "Desat",  -1300, 660)
    redmot = _reactive(mat, mpc, "ReduceMotion", -1300, 740)

    # Disp_eff = DispersionWidth * (1 + 0.8*Motion)
    m08    = _mul(mat, motion, _const(mat, 0.8, -1080, 520), -940, 510)
    onep   = _add(mat, _const(mat, 1.0, -1080, 560), m08, -800, 520)
    disp_e = _mul(mat, dispp, onep, -640, 460)

    # Phase = Time * Spin * (1 - ReduceMotion)
    tnode  = _expr(mat, unreal.MaterialExpressionTime, -1080, 700)
    spin_c = _const(mat, PRISM_SPIN, -1080, 760)
    sp     = _mul(mat, tnode, spin_c, -900, 710)
    freeze = _expr(mat, unreal.MaterialExpressionOneMinus, -900, 770)
    _wire(redmot, "", freeze, "")
    phase  = _mul(mat, sp, freeze, -720, 730)

    custom = _custom4(
        mat, _PRISM_HLSL,
        ["UV", "Phase", "Disp", "Spread", "Warm", "Desat", "RimBright", "TintA", "TintB"],
        -380, 120, "PrismCorona")
    _wire(uv, "", custom, "UV")
    _wire(phase, "", custom, "Phase")
    _wire(disp_e, "", custom, "Disp")
    _wire(spread, "", custom, "Spread")
    _wire(warm, "", custom, "Warm")
    _wire(desat, "", custom, "Desat")
    _wire(rimbr, "", custom, "RimBright")
    _wire(tint_a, "", custom, "TintA")
    _wire(tint_b, "", custom, "TintB")

    # split the float4: rgb → emissive, a → opacity/mask
    em = _expr(mat, unreal.MaterialExpressionComponentMask, -120, 60)
    _try_set(em, "r", True); _try_set(em, "g", True); _try_set(em, "b", True); _try_set(em, "a", False)
    _wire(custom, "", em, "")
    _wire_prop(em, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

    am = _expr(mat, unreal.MaterialExpressionComponentMask, -120, 220)
    _try_set(am, "r", False); _try_set(am, "g", False); _try_set(am, "b", False); _try_set(am, "a", True)
    _wire(custom, "", am, "")

    # Dark glass body: this is what OCCLUDES the white field so the ring reads as a
    # dark contour (the legibility-on-white fix) — the bright rainbow crest sits on top.
    darkbase = _expr(mat, unreal.MaterialExpressionConstant3Vector, -380, 600)
    _try_set(darkbase, "constant", unreal.LinearColor(0.03, 0.03, 0.045, 1.0))
    _wire_prop(darkbase, "", unreal.MaterialProperty.MP_BASE_COLOR)

    if PRISM_REFRACT:
        # glass body: opacity = band * GlassOpacity (occludes white); low IOR refraction.
        opac = _mul(mat, am, _scalar(mat, "GlassOpacity", PRISM_GLASS_OP, -380, 320), -120, 300)
        _wire_prop(opac, "", unreal.MaterialProperty.MP_OPACITY)
        ior = _scalar(mat, "GlassIOR", PRISM_IOR, -120, 400)
        _wire_prop(ior, "", unreal.MaterialProperty.MP_REFRACTION)
        _wire_prop(_const(mat, 0.06, -120, 470), "", unreal.MaterialProperty.MP_ROUGHNESS)
        _wire_prop(_const(mat, 1.0, -120, 540), "", unreal.MaterialProperty.MP_SPECULAR)
    else:
        _wire_prop(am, "", unreal.MaterialProperty.MP_OPACITY_MASK)

    MEL.layout_material_expressions(mat)
    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(PRISM_MAT_PATH)
    log("prism material built (refract={}) {}".format(PRISM_REFRACT, PRISM_MAT_PATH))
    return mat


def build_backdrop_material():
    """The high-key white field: UNLIT emissive at ~0.9 (headroom under 1.0). Also
    the scene's main soft light via Lumen GI."""
    if unreal.EditorAssetLibrary.does_asset_exist(BACKDROP_MAT_PATH):
        unreal.EditorAssetLibrary.delete_asset(BACKDROP_MAT_PATH)
    mat = _assets.create_asset(BACKDROP_MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
    if mat is None:
        _FAIL.append("create_asset None for backdrop material"); return None
    # DEFAULT_LIT white (lit by the bright key light) PLUS an emissive floor, so it reads
    # white whether the render path honours emissive-only or needs a lit surface. (The
    # all-emissive/unlit version rendered black in -game; lit is the proven-to-render path.)
    _try_set(mat, "shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
    _try_set(mat, "two_sided", True)
    lvl = PRISM_BACKDROP
    base = _expr(mat, unreal.MaterialExpressionConstant3Vector, -360, 0)
    _try_set(base, "constant", unreal.LinearColor(0.92, 0.92, 0.95, 1.0))
    _wire_prop(base, "", unreal.MaterialProperty.MP_BASE_COLOR)
    _wire_prop(_const(mat, 0.85, -360, 160), "", unreal.MaterialProperty.MP_ROUGHNESS)
    emis = _expr(mat, unreal.MaterialExpressionConstant3Vector, -360, 280)
    _try_set(emis, "constant", unreal.LinearColor(lvl * 0.5, lvl * 0.5, lvl * 0.55, 1.0))
    _wire_prop(emis, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(BACKDROP_MAT_PATH)
    log("backdrop material built (level={}) {}".format(lvl, BACKDROP_MAT_PATH))
    return mat


def build_slab_material():
    """The dark central form: an UNLIT near-black so it reads as a crisp flat dark
    SILHOUETTE against the white field (like the reference's dancer) — unlit so it
    needs no scene lighting / Lumen GI (this project has no mesh distance fields).
    A whisper of indigo keeps it from being a pure-black hole."""
    if unreal.EditorAssetLibrary.does_asset_exist(SLAB_MAT_PATH):
        unreal.EditorAssetLibrary.delete_asset(SLAB_MAT_PATH)
    mat = _assets.create_asset(SLAB_MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
    if mat is None:
        _FAIL.append("create_asset None for slab material"); return None
    _try_set(mat, "shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
    base = _expr(mat, unreal.MaterialExpressionConstant3Vector, -360, 0)
    _try_set(base, "constant", unreal.LinearColor(0.02, 0.02, 0.03, 1.0))
    _wire_prop(base, "", unreal.MaterialProperty.MP_BASE_COLOR)
    _wire_prop(_const(mat, 0.5, -360, 160), "", unreal.MaterialProperty.MP_ROUGHNESS)
    emis = _expr(mat, unreal.MaterialExpressionConstant3Vector, -360, 280)
    _try_set(emis, "constant", unreal.LinearColor(0.008, 0.010, 0.030, 1.0))
    _wire_prop(emis, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(SLAB_MAT_PATH)
    log("slab material built (unlit silhouette) {}".format(SLAB_MAT_PATH))
    return mat


_RIBBON_HLSL = r"""
// a flowing spectral strip: ROYGBIV along the length (UV.x), bright on the edges (UV.y),
// drifting with Phase. Output FLOAT4 = (rgb, edge-mask).
float u = UV.x, v = UV.y;
float edge = pow(abs(v*2.0 - 1.0), 2.0);           // bright at the two long edges
float3 roy = 0.5 + 0.5 * cos(6.2831853 * (u*1.5 + Phase*0.05 + float3(0.0, 0.33, 0.67)));
float3 spec = lerp(lerp(TintA.rgb, TintB.rgb, u), roy, saturate(Spread));
float3 col = spec * (0.30 + 1.7 * edge) * max(RimBright, 0.0) * 0.6;
float mask = saturate(0.30 + 0.7 * edge);
return float4(col, mask);
"""


def build_ribbon_material(mpc):
    if unreal.EditorAssetLibrary.does_asset_exist(RIBBON_MAT_PATH):
        unreal.EditorAssetLibrary.delete_asset(RIBBON_MAT_PATH)
    mat = _assets.create_asset(RIBBON_MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
    if mat is None:
        _FAIL.append("create_asset None for ribbon material"); return None
    _try_set(mat, "two_sided", True)
    _try_set(mat, "blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    _try_set(mat, "shading_model", unreal.MaterialShadingModel.MSM_UNLIT)

    uv     = _expr(mat, unreal.MaterialExpressionTextureCoordinate, -1100, -80)
    tint_a = _vec(mat, "RTintA", TINT_A, -1100, 40)
    tint_b = _vec(mat, "RTintB", TINT_B, -1100, 140)
    spread = _scalar(mat, "RHueSpread", PRISM_HUE_SPREAD, -1100, 240)
    rimbr  = _scalar(mat, "RRimBright", PRISM_RIM_BRIGHT, -1100, 320)
    redmot = _reactive(mat, mpc, "ReduceMotion", -1100, 420)
    tnode  = _expr(mat, unreal.MaterialExpressionTime, -1100, 520)
    sp     = _mul(mat, tnode, _const(mat, PRISM_SPIN, -1100, 580), -920, 530)
    freeze = _expr(mat, unreal.MaterialExpressionOneMinus, -920, 600); _wire(redmot, "", freeze, "")
    phase  = _mul(mat, sp, freeze, -740, 540)

    custom = _custom4(mat, _RIBBON_HLSL,
                      ["UV", "Phase", "Spread", "RimBright", "TintA", "TintB"],
                      -380, 120, "RibbonStrip")
    _wire(uv, "", custom, "UV"); _wire(phase, "", custom, "Phase")
    _wire(spread, "", custom, "Spread"); _wire(rimbr, "", custom, "RimBright")
    _wire(tint_a, "", custom, "TintA"); _wire(tint_b, "", custom, "TintB")

    em = _expr(mat, unreal.MaterialExpressionComponentMask, -120, 60)
    _try_set(em, "r", True); _try_set(em, "g", True); _try_set(em, "b", True); _try_set(em, "a", False)
    _wire(custom, "", em, ""); _wire_prop(em, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    am = _expr(mat, unreal.MaterialExpressionComponentMask, -120, 220)
    _try_set(am, "r", False); _try_set(am, "g", False); _try_set(am, "b", False); _try_set(am, "a", True)
    _wire(custom, "", am, ""); _wire_prop(am, "", unreal.MaterialProperty.MP_OPACITY)

    MEL.layout_material_expressions(mat)
    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(RIBBON_MAT_PATH)
    log("ribbon material built {}".format(RIBBON_MAT_PATH))
    return mat


# ---------------------------------------------------------------------------
# Scene geometry
#   World convention: camera at -X looking +X. Camera right = +Y (screen X),
#   camera up = +Z (screen Y). So "upper-right" = (+Y,+Z), "lower" = -Z.
# ---------------------------------------------------------------------------
def build_backdrop(mat):
    a = _actor_subsys.spawn_actor_from_class(
        unreal.StaticMeshActor, unreal.Vector(3400.0, 0.0, 250.0),
        unreal.Rotator(0.0, -90.0, 0.0))                 # plane normal +Z → -X (faces camera)
    smc = a.static_mesh_component
    smc.set_static_mesh(unreal.EditorAssetLibrary.load_asset(PLANE_ASSET))
    a.set_actor_scale3d(unreal.Vector(220.0, 220.0, 1.0))
    if mat:
        smc.set_material(0, mat)
    _try_set(smc, "cast_shadow", False)
    _label(a, "Backdrop")


SPHERE_ASSET = "/Engine/BasicShapes/Sphere.Sphere"


def build_dark_form(mat):
    """An ORGANIC dark form (replaces the lifeless rectangle): a leaning, sinuous column
    of overlapping stretched spheres — a soft abstract presence the arcs swirl around, NOT
    a literal figure. Slightly screen-left at the base, tapering upward like a flame/dancer."""
    # MINIMAL / SECONDARY (Corey's call): a SMALL, soft, smooth dark form low in the frame —
    # a quiet anchor the swept arcs are the hero around, NOT the main event. Tight spacing
    # (<< 2·radius) fuses the ovoids into a smooth little sprout rising from the lower third.
    n = 8
    sphere = unreal.EditorAssetLibrary.load_asset(SPHERE_ASSET)
    for i in range(n):
        t = i / float(n - 1)                                  # 0..1 base→top
        z = -340.0 + t * 470.0                                # LOW: from below frame up into the lower third
        y = -30.0 + 70.0 * math.sin(t * math.pi * 0.9)        # slight lean
        x = 420.0 + 40.0 * math.sin(t * math.pi)              # gentle bow
        s = 1.45 - 0.90 * t                                   # small, tapering to a soft tip
        a = _actor_subsys.spawn_actor_from_class(
            unreal.StaticMeshActor, unreal.Vector(x, y, z), unreal.Rotator(0.0, 0.0, 0.0))
        smc = a.static_mesh_component
        smc.set_static_mesh(sphere)
        a.set_actor_scale3d(unreal.Vector(s * 0.80, s * 0.80, s * 1.75))   # smooth small column
        if mat:
            smc.set_material(0, mat)
        _try_set(smc, "cast_shadow", False)
        _label(a, "DarkForm_{}".format(i))
    log("dark form: {} fused ovoids (small low anchor — arcs are the hero)".format(n))


# SWEPT-ARC clusters mirroring the reference's warped-vinyl swirls: two "hand swirls"
# (upper-right + upper-left) and a big sweep across the lower third that wraps the form.
# Each arc = a camera-facing quad; the material draws ONE soft streaked arc, the quad's
# ROLL sets the sweep clock-position and SCALE its size. Nested arcs per cluster = the
# record-groove layering. centre=(x,y,z screen-depth); arcs=[(scale, dy, dz, roll, yaw)].
# BIG ASYMMETRIC sweeps (Corey's call) wrapping the small low form — like the reference's
# spinning-disc swirls. Each cluster = nested arcs sweeping across ~half the frame; the
# three clusters face DIFFERENT directions (varied rolls) so the composition is dynamic,
# not a mirror pair. scale ~16–26 → arc radius ~530–860u at the arcs' depth.
_ARC_CLUSTERS = [
    # upper-right swirl, sweeping down toward the centre (over the form)
    dict(name="SwirlR", centre=(720.0, 560.0, 520.0),
         arcs=[(16.0, 0, 0, 158, -7), (20.0, 60, -40, 172, 6), (24.0, -50, 55, 142, -5)]),
    # upper-left swirl, sweeping the other way — different angle, not a mirror
    dict(name="SwirlL", centre=(820.0, -640.0, 540.0),
         arcs=[(17.0, 0, 0, 28, 9), (21.0, -60, 35, 8, -7), (25.0, 55, -30, 48, 8)]),
    # big lower sweep wrapping UNDER/around the form across the bottom third
    dict(name="Sweep",  centre=(560.0, -40.0, -120.0),
         arcs=[(20.0, -220, 0, 248, 13), (24.0, 120, 40, 262, -9), (28.0, 360, -30, 236, 15)]),
]


def build_arcs(mat):
    """Place the swept-arc quads. Replaces the flat concentric-ring coronas + straight
    spiral ribbon with one cohesive family of soft, streaked, edge-dispersive arcs."""
    total = 0
    for cl in _ARC_CLUSTERS:
        cx, cy, cz = cl["centre"]
        for i, (scl, dy, dz, roll, yaw) in enumerate(cl["arcs"]):
            a = _actor_subsys.spawn_actor_from_class(
                unreal.StaticMeshActor, unreal.Vector(cx, cy + dy, cz + dz),
                unreal.Rotator(float(roll), -90.0, float(yaw)))   # roll=clock, pitch=-90 face cam, yaw=tilt
            smc = a.static_mesh_component
            smc.set_static_mesh(unreal.EditorAssetLibrary.load_asset(PLANE_ASSET))
            a.set_actor_scale3d(unreal.Vector(float(scl), float(scl), 1.0))
            if mat:
                smc.set_material(0, mat)
            _try_set(smc, "cast_shadow", False)
            _label(a, "{}_{}".format(cl["name"], i))
            total += 1
    log("arcs: {} swept quads across {} clusters".format(total, len(_ARC_CLUSTERS)))


def build_lighting():
    """High-key + UNLIT/emissive by design: the unlit white backdrop and the self-lit
    emissive rings carry the whole image, so the scene needs NO dynamic GI and NO
    SkyLight (a real-time-capture SkyLight with no SkyAtmosphere just errors + goes
    black). One soft movable directional remains only to give the translucent glass
    BODIES a faint shading gradient (it never errors; harmless if unused). No fog."""
    sun = _actor_subsys.spawn_actor_from_class(
        unreal.DirectionalLight, unreal.Vector(0.0, 0.0, 800.0),
        unreal.Rotator(0.0, -12.0, 0.0))   # travels +X (from behind camera) → lights the front of the scene
    lc = sun.get_component_by_class(unreal.DirectionalLightComponent)
    if lc:
        _try_set(lc, "mobility", unreal.ComponentMobility.MOVABLE)
        _try_set(lc, "intensity", 5.0)
        _try_set(lc, "light_color", unreal.LinearColor(0.95, 0.97, 1.0, 1.0))  # near-white
        _try_set_first(lc, ("cast_shadows", "casts_dynamic_shadow"), True)
    _label(sun, "KeyLight")
    log("lighting: LIT white backdrop + bright frontal directional + emissive rings (Indigo-proven render path)")


def build_post():
    ppv = _actor_subsys.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector(0.0, 0.0, 0.0))
    ppv.set_editor_property("unbound", True)
    s = ppv.get_editor_property("settings")
    # Pin LUMEN GI + Reflections ON — exactly like the known-good Indigo scene (which
    # renders in -game on this project). GI=NONE renders BLACK in the -game deferred view
    # here even though the editor SceneCapture path is fine; matching Indigo's Lumen is the
    # fix. (Unlit/emissive content still carries the look; Lumen GI bounce is a bonus.)
    _try_set(s, "override_dynamic_global_illumination_method", True)
    _try_set(s, "dynamic_global_illumination_method", unreal.DynamicGlobalIlluminationMethod.LUMEN)
    _try_set(s, "override_reflection_method", True)
    _try_set(s, "reflection_method", unreal.ReflectionMethod.LUMEN)
    # Manual exposure (auto "breathes" — fatal for calm). High-key sits near bias 0.
    _try_set(s, "override_auto_exposure_method", True)
    _try_set(s, "auto_exposure_method", unreal.AutoExposureMethod.AEM_MANUAL)
    _try_set(s, "override_auto_exposure_bias", True)
    _try_set(s, "auto_exposure_bias", PRISM_EXP_BIAS)
    # *** THE "-game renders BLACK" FIX (root cause, verified against Scene.h) ***
    # In AEM_MANUAL, "Apply Physical Camera Exposure" (default ON) makes the exposure
    # use the player camera's PHYSICAL settings (f/2.8, 1/60s, ISO100 ≈ EV100 ~9),
    # which crushes a high-key scene (emissive ~1.0) to BLACK in -game. A
    # SceneCaptureComponent2D has NO physical camera, so the capture preview looked
    # correctly WHITE — that asymmetry (capture white, -game black, SAME map) was the
    # whole bug. Turn physical-camera exposure OFF so -game exposure == the proven
    # capture exposure, and pin metering (manual ignores it, but mirrors capture_shot.py).
    _try_set(s, "override_auto_exposure_apply_physical_camera_exposure", True)
    _try_set(s, "auto_exposure_apply_physical_camera_exposure", False)
    _try_set(s, "override_auto_exposure_min_brightness", True)
    _try_set(s, "auto_exposure_min_brightness", 1.0)
    _try_set(s, "override_auto_exposure_max_brightness", True)
    _try_set(s, "auto_exposure_max_brightness", 1.0)
    # Bloom — the rim SHOULD bloom (half the reference read). Threshold 1.0 so only
    # the >1 emissive rim blooms, not the 0.9 white field.
    _try_set(s, "override_bloom_intensity", True)
    _try_set(s, "bloom_intensity", PRISM_BLOOM)
    _try_set(s, "override_bloom_threshold", True)
    _try_set(s, "bloom_threshold", PRISM_BLOOM_THRESH)
    _try_set(s, "override_film_grain_intensity", True)
    _try_set(s, "film_grain_intensity", 0.08)
    ppv.set_editor_property("settings", s)
    _label(ppv, "PostFX")
    log("post: AEM_MANUAL bias={} physcam-exposure=OFF min=max=1.0 lumen=on bloom={}".format(
        PRISM_EXP_BIAS, PRISM_BLOOM))


CAM_LOC = unreal.Vector(-2200.0, 60.0, 300.0)
CAM_ROT = unreal.Rotator(0.0, 3.0, 0.0)               # (roll, pitch up, yaw) — look +X


def build_camera():
    cam = _actor_subsys.spawn_actor_from_class(unreal.CameraActor, CAM_LOC, CAM_ROT)
    cc = cam.get_component_by_class(unreal.CameraComponent)
    if cc:
        _try_set(cc, "field_of_view", 52.0)
        _try_set(cc, "constrain_aspect_ratio", False)  # fill ultrawide (no pillarbox)
    _try_set(cam, "auto_activate_for_player", unreal.AutoReceiveInput.PLAYER0)
    _label(cam, "Camera")
    # A PlayerStart at the camera transform: without one, a cooked/-game run logs
    # "NO PLAYERSTART" and spawns the default pawn at the world ORIGIN looking at
    # nothing → a BLACK wallpaper. With it, the -game view matches the camera even if
    # auto-activation doesn't fire. (The offscreen SceneCapture preview is immune — it
    # spawns its own camera — which is why this only bit the live wallpaper.)
    try:
        ps = _actor_subsys.spawn_actor_from_class(unreal.PlayerStart, CAM_LOC, CAM_ROT)
        _label(ps, "PlayerStart")
    except Exception as exc:  # noqa: BLE001
        log("PlayerStart spawn skipped: {}".format(exc))
    return cam


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
def main():
    if unreal.EditorAssetLibrary.does_asset_exist(LEVEL_PATH):
        _level_subsys.load_level(LEVEL_PATH)
        log("loaded existing level {}".format(LEVEL_PATH))
    else:
        try:
            created = _level_subsys.new_level(LEVEL_PATH, is_partitioned_world=False)
        except TypeError:
            created = _level_subsys.new_level(LEVEL_PATH)
        log("new_level({}, non-partitioned) -> {}".format(LEVEL_PATH, created))
        if not created:
            _FAIL.append("new_level returned False — level not created")

    clear_prior()

    mpc        = ensure_mpc()
    prism_mat  = build_prism_material(mpc)   # now the SWEPT-ARC material
    back_mat   = build_backdrop_material()
    slab_mat   = build_slab_material()

    build_backdrop(back_mat)
    build_dark_form(slab_mat)                # organic sphere column (was the rectangle)
    build_arcs(prism_mat)                    # swirl clusters (was rings + ribbon)
    build_lighting()
    build_post()
    build_camera()

    saved = _level_subsys.save_current_level()
    log("save_current_level() -> {}".format(saved))
    if not saved:
        _FAIL.append("save_current_level returned False")

    if _FAIL:
        log("BUILD HAD {} FAILURE(S) — NOT emitting success marker:".format(len(_FAIL)))
        for f in _FAIL:
            log("  FAIL: {}".format(f))
    else:
        # the success MARK the watchdog greps for (keep in sync with author_scene.sh MARK).
        log("Prism Field scene built at {}".format(LEVEL_PATH))


if __name__ == "__main__":
    main()
else:
    main()
