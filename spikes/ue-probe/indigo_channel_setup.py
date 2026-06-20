# indigo_channel_setup.py — author "The Indigo Channel" dark-ride tableau.
#
# WHAT THIS IS (ADR-0023, the DIMENSIONAL pivot — supersedes the flat
# gradient_wave_setup.py "shader on a wall", which the user rejected because
# nothing in it needed Unreal: a 2D shader did it cheaper.)
#
#   A volumetric-fog ROOM, not a wall. One cool CYAN directional light BACKLIGHTS
#   the fog from the far end of the channel, shining BACK toward the camera (this is
#   00090's focal glow, now an actual light in 3-space). The fog glows in the
#   backlight and is CUT by a few dark monolith "blades" receding into depth — the
#   blades become silhouettes and carve the light into real god-rays + genuine
#   parallax; the fog is the indigo body the cyan blooms through. BACKLIGHTING IS
#   LOAD-BEARING: god-rays only exist when fog is lit from BEHIND the occluders
#   toward the eye (front-lighting gave ZERO beams). Depth-of-field is REAL
#   Cinematic DoF (blurs by world distance), NOT the analytic shimmer-bokeh that
#   got rejected.
#
#   The violet that landed in 00090 emerges PHYSICALLY here — where the cyan
#   directional scatter overlaps the indigo fog inscatter — which is exactly why
#   it reads deeper than any 2D lerp, and why the scene earns the Lumen tax.
#
#   Design: art-director "Indigo Channel" spec (2026-06-19), built on the proven
#   UE5.8-Python patterns from gradient_wave_setup.py (idempotent clear, tolerant
#   property setters, _FAIL accumulator so a broken scene never reaches PASS,
#   manual-exposure PPV, plain CameraActor — a CineCamera crushes a dim scene to
#   black in -game/packaged).
#
# HOW TO RUN IT (headless, same harness as gradient_wave_setup.py)
#   SCENE_SCRIPT=indigo_channel_setup.py MARK='Indigo Channel scene built' \
#     bash spikes/ue-probe/author_scene.sh
#   (author_scene.sh adds the VRAM pre-flight gate + watchdog. The card is SHARED;
#    it aborts unless >= MIN_FREE_MIB free so we never OOM-collide the user's gens.
#    The authoring spike here is small — MIN_FREE_MIB=12000 is plenty.)
#
#   Authors INTO /Game/AgentOS/CalmWallpaper (the SAME map the whole measure/
#   coexist/frametime harness points at), replacing the prior scene's contents.
#
# LIVE LOOK (the thing the user actually wants — see it moving on the desktop):
#   ~/UnrealEngine/Engine/Binaries/Linux/UnrealEditor \
#     ~/UnrealProjects/AgentOSBlank/AgentOSBlank.uproject /Game/AgentOS/CalmWallpaper \
#     -game -windowed -ResX=1600 -ResY=900 -nosplash -nosound
#   (run via Bash run_in_background:true, NOT setsid; kill with
#    pkill -9 -f '[B]inaries/Linux/UnrealEditor')
#
# TUNE WITHOUT EDITING CODE (env knobs — defaults are the values that landed live):
#   INDIGO_EXP_BIAS      manual exposure stops      (default -3.0; backlit + bright light)
#   INDIGO_LIGHT_INT     directional light lux       (default 2000.0; bright so fog scatters)
#   INDIGO_LIGHT_PITCH   light downward tilt deg     (default -20.0)
#   INDIGO_LIGHT_YAW     light horizontal dir deg    (default 160.0; ~180 = backlight toward cam)
#   INDIGO_VOL_SCATTER   god-ray strength            (default  2.0)
#   INDIGO_FOG_DENSITY   fog thickness               (default  0.22; dense enough for AIRBORNE shafts)
#   INDIGO_DOF_FOCAL     focal distance cm           (default 2700.0 -> mid blades)
#   INDIGO_DOF_FSTOP     aperture (low = shallow)    (default  2.0)
#   INDIGO_BLOOM         bloom intensity             (default  0.40)
#   INDIGO_CAM_X         camera X (behind near blade)(default -2500.0)
#   INDIGO_FOV           camera FOV deg              (default 75.0)
#   INDIGO_MOTION        1 = author the parallax LevelSequence (default OFF = static)
#   INDIGO_MOTION_SPEED  motion rate multiplier      (default 1.0; 0 = FREEZE, the reduce-motion/throttle seam)
#
# MOTION: the slow camera parallax dolly + light-breath is a looping LevelSequence
#   (build_camera_motion), auto-played + looped in -game. Gated behind INDIGO_MOTION
#   (default OFF). INDIGO_MOTION_SPEED=0 freezes to a held pose (the reduce-motion /
#   GPU-throttle seam) with no re-author.

import os

import unreal

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
AGENTOS_PREFIX = "AgentOS_"                       # actor-label idempotency tag
LEVEL_PATH = "/Game/AgentOS/CalmWallpaper"        # SAME map the harness measures
MAT_DIR    = "/Game/AgentOS/Materials"
SLAB_MAT_NAME = "M_AgentOS_Slab"
SLAB_MAT_PATH = MAT_DIR + "/" + SLAB_MAT_NAME

CUBE_ASSET  = "/Engine/BasicShapes/Cube.Cube"     # blade occluders
PLANE_ASSET = "/Engine/BasicShapes/Plane.Plane"   # fog floor

# --- Palette (LINEAR — indigo->violet->teal->cyan, the locked 00090 mood) ------
# The gradient is produced PHYSICALLY: cyan light scatters through indigo fog ->
# violet bloom; far blades fall to abyss. Nothing here carries warm chroma —
# warmth stays reserved for the "needs-you" signal (visual-systems lock).
SLAB_BASE = unreal.LinearColor(0.010, 0.012, 0.020, 1.0)  # near-black blade albedo
SLAB_EMIS = unreal.LinearColor(0.008, 0.010, 0.022, 1.0)  # whisper lift: form, not a hole
FOG_INSCATTER = unreal.LinearColor(0.020, 0.022, 0.060, 1.0)  # the indigo-violet body
# Light colour: cyan focal core #6FC8DE. UE light_color wants sRGB FColor (0-255).
# CRITICAL: unreal.Color POSITIONAL args are (b, g, r, a) — FColor is BGRA! Passing
# (111,200,222) made a WARM light (r=222,g=200,b=111). Use KEYWORDS to be safe.
LIGHT_COLOR = unreal.Color(r=111, g=200, b=222, a=255)
# Volumetric fog albedo: cool, keeps inscatter in the cyan-violet family (FColor).
# Same BGRA gotcha — KEYWORD args (positional unreal.Color is (b,g,r,a)).
FOG_ALBEDO = unreal.Color(r=201, g=224, b=242, a=255)

# Blades: thin in X (depth axis the fog + DoF act on), tall in Z, staggered in
# Y for lateral parallax, staggered in X for progressive shaft occlusion.
# (X fwd, Y lateral, Z up) ; scale (X thin ~40cm, Y width, Z height)
SLABS = [
    ("BladeNear", unreal.Vector(-1200.0,  900.0,   0.0), unreal.Vector(0.4,  6.0, 14.0)),
    ("BladeMid",  unreal.Vector(  200.0, -300.0,   0.0), unreal.Vector(0.4,  7.0, 16.0)),
    ("BladeBack", unreal.Vector( 1600.0,  600.0, 100.0), unreal.Vector(0.4,  8.0, 18.0)),
    ("BladeFar",  unreal.Vector( 3200.0, -200.0, 200.0), unreal.Vector(0.4, 10.0, 22.0)),
]

# --- Env-tunable look knobs ----------------------------------------------------
# Exposure DECOUPLING (design-technologist audit): the look is "shaft = brightest
# element in a dark room". You cannot get that by sweeping exposure with a dim light
# — a 10-lux source ÷ exposure puts the volumetric inscatter below the display's
# first code value (that was the "no shaft" bug). Instead: BRIGHT light gives the fog
# real photons, NEGATIVE exposure keeps the directly-lit blades dark. AEM_MANUAL
# mapping is multiplier = 2^bias (bias 0 = 1x, -2 = 0.25x). Bracket bias in {-3..0}.
EXP_BIAS     = float(os.environ.get("INDIGO_EXP_BIAS",    "-3.0"))   # backlit -game runtime renders brighter than editor preview; -3 = moodier (verify live)
LIGHT_INT    = float(os.environ.get("INDIGO_LIGHT_INT",   "2000.0"))
# BACKLIGHT geometry: god-rays only exist when the fog is lit FROM BEHIND the
# occluders, toward the camera. Camera sits at -X looking +X down the channel, so
# the light must shine back toward it (yaw ~180 = forward -X) from the far end, tilted
# down. yaw 160 keeps a diagonal rake; pitch -25 sends the shafts down through frame.
LIGHT_PITCH  = float(os.environ.get("INDIGO_LIGHT_PITCH", "-20.0"))  # flatter so shafts rake across frame, not down into the floor
LIGHT_YAW    = float(os.environ.get("INDIGO_LIGHT_YAW",   "160.0"))
VOL_SCATTER  = float(os.environ.get("INDIGO_VOL_SCATTER", "2.0"))   # bright light -> drop scatter or it over-blooms to a white cloud
FOG_DENSITY  = float(os.environ.get("INDIGO_FOG_DENSITY", "0.22"))   # denser still, so the light reads as AIRBORNE shafts, not just floor-glow
DOF_FOCAL    = float(os.environ.get("INDIGO_DOF_FOCAL",   "2700.0"))
DOF_FSTOP    = float(os.environ.get("INDIGO_DOF_FSTOP",   "2.0"))
BLOOM        = float(os.environ.get("INDIGO_BLOOM",       "0.40"))
CAM_X        = float(os.environ.get("INDIGO_CAM_X",       "-2500.0"))
FOV          = float(os.environ.get("INDIGO_FOV",         "75.0"))

# --- Motion (v2, the dark-ride parallax — see build_camera_motion) -------------
# OFF by default so the static-look iteration is unaffected; flip INDIGO_MOTION=1
# (or true/yes/on) when authoring the live version. A motion-build FAILURE is
# logged but NOT appended to _FAIL — a static scene is still a valid PASS.
def _envflag(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")

MOTION_ON    = _envflag("INDIGO_MOTION", default=False)
# GlobalSpeed scalar multiplying ALL motion. 1.0 = authored pace; 0.0 = FREEZE
# (reduce-motion / GPU-throttle seam). A -game auto-play LevelSequence runs at a
# fixed wall-clock rate, so "speed" is baked into the keyframe TIME spacing at
# author time: faster speed -> shorter periods -> keys packed closer. 0.0 collapses
# the loop to a single held frame (no motion at all), which is the freeze.
MOTION_SPEED = float(os.environ.get("INDIGO_MOTION_SPEED", "1.0"))

SEQ_DIR      = "/Game/AgentOS/Sequences"
SEQ_NAME     = "SEQ_AgentOS_IndigoMotion"
SEQ_PATH     = SEQ_DIR + "/" + SEQ_NAME
SEQ_FPS      = int(os.environ.get("INDIGO_SEQ_FPS", "30"))  # display rate; low is fine for slow drift

# Motion amplitudes + INCOMMENSURATE periods (sec). Co-prime-ish ratios so the
# combined camera path never visibly tiles inside a human attention span.
CAM_DOLLY_Y_AMP   = float(os.environ.get("INDIGO_CAM_DOLLY_AMP",   "120.0"))  # cm lateral
CAM_DOLLY_Y_SEC   = float(os.environ.get("INDIGO_CAM_DOLLY_SEC",   "41.0"))   # period
CAM_PITCH_AMP     = float(os.environ.get("INDIGO_CAM_PITCH_AMP",   "0.4"))    # deg bob
CAM_PITCH_SEC     = float(os.environ.get("INDIGO_CAM_PITCH_SEC",   "53.0"))   # period (incommensurate w/ dolly)
LIGHT_YAW_AMP     = float(os.environ.get("INDIGO_LIGHT_YAW_AMP",   "1.5"))    # deg breath
LIGHT_YAW_SEC     = float(os.environ.get("INDIGO_LIGHT_YAW_SEC",   "67.0"))   # period (incommensurate w/ both)

# ---------------------------------------------------------------------------
# Subsystems / helpers
# ---------------------------------------------------------------------------
_actor_subsys = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
_level_subsys = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
_assets       = unreal.AssetToolsHelpers.get_asset_tools()
MEL           = unreal.MaterialEditingLibrary

# Build-failure accumulator: ANY hard failure makes main() skip the success
# marker, so author_scene.sh's watchdog reports FAIL (a broken scene must never
# PASS). Cosmetic property drift (renamed PPV/fog props) logs + skips instead.
_FAIL = []


def log(msg):
    unreal.log("[AgentOS indigo_channel] " + str(msg))


def _label(actor, name):
    actor.set_actor_label(AGENTOS_PREFIX + name)
    return actor


def _try_set(obj, prop, value):
    """Tolerant set_editor_property for COSMETIC props (exposure/fog/DoF/light):
    a renamed property in this UE point-release logs + skips rather than aborting
    the whole build. Tunable visuals, not load-bearing structure."""
    try:
        obj.set_editor_property(prop, value)
        return True
    except Exception as exc:  # noqa: BLE001
        log("skip cosmetic set {}={!r}: {}".format(prop, value, exc))
        return False


def _try_set_first(obj, props, value):
    """Try several candidate property names (API drift across 5.x); use the first
    that sticks. Returns the name that worked, or None."""
    for p in props:
        if _try_set(obj, p, value):
            return p
    return None


# Environment actors we fully OWN in this dedicated wallpaper level. scene_setup.py
# left a golden-hour sun + SkyAtmosphere + SkyLight that were NOT AgentOS_-labelled;
# the old full-frame wall hid them, but this open room EXPOSES them — they wash the
# dark scene bright and inject the reserved warm hue. Sweep them: clear runs BEFORE
# we build ours, and this level's lighting is entirely ours.
_ENV_CLASSES = (
    unreal.SkyAtmosphere, unreal.SkyLight, unreal.DirectionalLight,
    unreal.ExponentialHeightFog, unreal.PostProcessVolume,
)
_ENV_NAME_HINTS = ("sky", "sun", "atmospher")  # also catch BP sky-sphere / source actors


def _is_env_leftover(actor):
    if isinstance(actor, _ENV_CLASSES):
        return True
    nm = (actor.get_actor_label() + " " + actor.get_class().get_name()).lower()
    return any(h in nm for h in _ENV_NAME_HINTS)


def clear_prior():
    """Destroy our prior actors AND any pre-existing environment actors (leftover
    sun/sky/fog/PPV from earlier scenes) — a clean DARK room is the only correct
    starting state, since the cyan rake is meant to be the only light."""
    removed = 0
    for actor in _actor_subsys.get_all_level_actors():
        try:
            if actor.get_actor_label().startswith(AGENTOS_PREFIX) or _is_env_leftover(actor):
                _actor_subsys.destroy_actor(actor)
                removed += 1
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            log("skip actor during clear: {}".format(exc))
    log("cleared {} actor(s) (ours + env leftovers)".format(removed))


# ---- material helpers -----------------------------------------------------
def _expr(mat, cls, x, y):
    return MEL.create_material_expression(mat, cls, x, y)


def _wire_prop(a, a_out, prop):
    ok = MEL.connect_material_property(a, a_out, prop)
    if not ok:
        msg = "PROP WIRE FAILED: {}.{!r} -> {}".format(
            a.get_class().get_name(), a_out, prop)
        log(msg)
        _FAIL.append(msg)


def build_slab_material():
    """A simple default-LIT dark material for the blades: near-black albedo so
    they read as silhouettes, a moderate roughness so the cyan rake leaves a cool
    rim, and a whisper of indigo emissive so a blade is never a pure-black hole
    (replaces a SkyLight — cheaper + headless-robust)."""
    if unreal.EditorAssetLibrary.does_asset_exist(SLAB_MAT_PATH):
        unreal.EditorAssetLibrary.delete_asset(SLAB_MAT_PATH)
        log("deleted prior slab material {}".format(SLAB_MAT_PATH))

    mat = _assets.create_asset(SLAB_MAT_NAME, MAT_DIR, unreal.Material,
                               unreal.MaterialFactoryNew())
    if mat is None:
        _FAIL.append("create_asset returned None for {}".format(SLAB_MAT_PATH))
        return None
    # default shading model is MSM_DEFAULT_LIT, blend OPAQUE — exactly what we want.

    base = _expr(mat, unreal.MaterialExpressionConstant3Vector, -360, 0)
    base.set_editor_property("constant", SLAB_BASE)
    _wire_prop(base, "", unreal.MaterialProperty.MP_BASE_COLOR)

    rough = _expr(mat, unreal.MaterialExpressionConstant, -360, 180)
    rough.set_editor_property("r", 0.55)
    _wire_prop(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)

    emis = _expr(mat, unreal.MaterialExpressionConstant3Vector, -360, 320)
    emis.set_editor_property("constant", SLAB_EMIS)
    _wire_prop(emis, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

    MEL.layout_material_expressions(mat)
    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(SLAB_MAT_PATH)
    log("slab material built + saved: {}".format(SLAB_MAT_PATH))
    return mat


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------
def build_blades(mat):
    """The few dark monolith blades that occlude the shaft into god-rays and
    create real depth parallax. Thin in X, tall in Z, staggered in depth."""
    mesh = unreal.EditorAssetLibrary.load_asset(CUBE_ASSET)
    if mesh is None:
        _FAIL.append("could not load cube mesh {}".format(CUBE_ASSET))
        return
    for name, loc, scale in SLABS:
        actor = _actor_subsys.spawn_actor_from_class(
            unreal.StaticMeshActor, loc, unreal.Rotator(0.0, 0.0, 0.0))
        smc = actor.static_mesh_component
        smc.set_static_mesh(mesh)
        actor.set_actor_scale3d(scale)
        # the occluder must cast shadow into the fog — that shadow IS the ray gap.
        _try_set(smc, "cast_shadow", True)
        if mat is not None:
            smc.set_material(0, mat)
        _label(actor, name)
    log("built {} blades".format(len(SLABS)))


def build_floor(mat):
    """A dark floor for the fog to sit on and the god-rays to graze (grounds the
    shaft so it reads as a space, not a void)."""
    mesh = unreal.EditorAssetLibrary.load_asset(PLANE_ASSET)
    if mesh is None:
        log("no plane mesh; skipping floor")
        return
    actor = _actor_subsys.spawn_actor_from_class(
        unreal.StaticMeshActor, unreal.Vector(800.0, 0.0, -120.0),
        unreal.Rotator(0.0, 0.0, 0.0))
    smc = actor.static_mesh_component
    smc.set_static_mesh(mesh)
    actor.set_actor_scale3d(unreal.Vector(80.0, 80.0, 1.0))
    if mat is not None:
        smc.set_material(0, mat)
    _label(actor, "Floor")


def build_light():
    """The ONE cyan directional light: the focal glow AND the only shaft source.
    Movable + volumetric scatter + volumetric shadow so the blades carve real
    god-rays. Must read under AEM_MANUAL bias ~10."""
    light = _actor_subsys.spawn_actor_from_class(
        unreal.DirectionalLight, unreal.Vector(0.0, 0.0, 600.0),
        unreal.Rotator(0.0, LIGHT_PITCH, LIGHT_YAW))   # (roll, pitch, yaw)
    comp = light.get_component_by_class(unreal.DirectionalLightComponent)
    if comp is None:
        _FAIL.append("DirectionalLight has no DirectionalLightComponent")
        return None
    # Movable: full dynamic Cascaded Shadow Maps — and CSM is what carves a
    # DIRECTIONAL light's shaft into rays (NOT the per-light cast_volumetric_shadow
    # flag, which is the point/spot knob — dropped).
    _try_set(comp, "mobility", unreal.ComponentMobility.MOVABLE)
    _try_set(comp, "intensity", LIGHT_INT)
    _try_set(comp, "light_color", LIGHT_COLOR)
    # inscatter strength (scales fog intensity AND the light colour into the medium).
    _try_set(comp, "volumetric_scattering_intensity", VOL_SCATTER)
    # CSM essentials: without a shadow-casting light + a dynamic-shadow distance that
    # covers the ~3200cm-deep channel, the blades can't occlude the shaft -> glow, no rays.
    _try_set_first(comp, ("cast_shadows", "casts_dynamic_shadow"), True)
    _try_set(comp, "dynamic_shadow_distance_movable_light", 8000.0)
    _try_set(comp, "use_inset_shadows_for_movable_objects", True)
    _label(light, "KeyLight")
    log("light: cyan rake pitch={} yaw={} int={} vol_scatter={}".format(
        LIGHT_PITCH, LIGHT_YAW, LIGHT_INT, VOL_SCATTER))
    return light


def build_fog():
    """Thin cool volumetric fog — the atmosphere the shaft lives in and the
    indigo-violet body the cyan blooms through. LOW density; the look is scatter,
    not soup."""
    fog = _actor_subsys.spawn_actor_from_class(
        unreal.ExponentialHeightFog, unreal.Vector(0.0, 0.0, 0.0))
    fc = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
    if fc is None:
        _FAIL.append("ExponentialHeightFog has no component")
        return
    _try_set(fc, "fog_density", FOG_DENSITY)
    _try_set(fc, "fog_height_falloff", 0.12)
    _try_set_first(
        fc, ("fog_inscattering_luminance", "fog_inscattering_color", "inscattering_color"),
        FOG_INSCATTER)
    # the volumetric path is what actually makes the shaft. UE5.8 Python name is
    # `enable_volumetric_fog` (b-prefix stripped from bEnableVolumetricFog) — plain
    # `volumetric_fog` does NOT exist and silently no-ops -> NO god-rays. This is
    # load-bearing, not cosmetic: a fog-less Indigo Channel must FAIL, not PASS.
    if not _try_set_first(fc, ("enable_volumetric_fog", "volumetric_fog",
                               "b_enable_volumetric_fog"), True):
        _FAIL.append("could not enable volumetric fog — no god-rays without it")
    _try_set(fc, "volumetric_fog_scattering_distribution", 0.85)  # forward-scatter -> tight shafts
    _try_set(fc, "volumetric_fog_albedo", FOG_ALBEDO)
    _try_set(fc, "volumetric_fog_distance", 6000.0)
    _try_set(fc, "volumetric_fog_extinction_scale", 1.0)
    _label(fog, "Fog")
    log("fog: density={} volumetric=on scatter_dist=0.85".format(FOG_DENSITY))


def build_post():
    """Manual-exposure PPV (blacks stay black, palette renders 1:1) + REAL
    Cinematic DoF that blurs by world distance (near blade + far fog go creamy,
    mid stays crisp) — the depth-of-field the rejected sparkle-bokeh only faked.
    Restrained bloom (the shaft is already a glow) + subtle grain (anti-banding)."""
    ppv = _actor_subsys.spawn_actor_from_class(
        unreal.PostProcessVolume, unreal.Vector(0.0, 0.0, 0.0))
    ppv.set_editor_property("unbound", True)
    s = ppv.get_editor_property("settings")

    # Fixed manual exposure: auto washes + "breathes" (fatal for calm); manual-0
    # is black. AEM_MANUAL with a tuned bias is the keeper.
    _try_set(s, "override_auto_exposure_method", True)
    _try_set(s, "auto_exposure_method", unreal.AutoExposureMethod.AEM_MANUAL)
    _try_set(s, "override_auto_exposure_bias", True)
    _try_set(s, "auto_exposure_bias", EXP_BIAS)

    # Cinematic DoF (physical): blurs by the real depth buffer — needs the blades
    # + fog depth to read, which this scene has (unlike the flat wall).
    _try_set(s, "override_depth_of_field_focal_distance", True)
    _try_set(s, "depth_of_field_focal_distance", DOF_FOCAL)
    _try_set(s, "override_depth_of_field_fstop", True)
    _try_set(s, "depth_of_field_fstop", DOF_FSTOP)
    _try_set(s, "override_depth_of_field_sensor_width", True)
    _try_set(s, "depth_of_field_sensor_width", 24.576)

    # Bloom: only the bright shaft core should bloom, not the whole fog.
    _try_set(s, "override_bloom_intensity", True)
    _try_set(s, "bloom_intensity", BLOOM)
    _try_set(s, "override_bloom_threshold", True)
    _try_set(s, "bloom_threshold", 1.0)

    # Film grain: subtle, doubles as anti-banding on the dark gradient.
    _try_set(s, "override_film_grain_intensity", True)
    _try_set(s, "film_grain_intensity", 0.15)

    ppv.set_editor_property("settings", s)
    _label(ppv, "PostFX")
    log("post: exp_bias={} dof(focal={},f/{}) bloom={}".format(
        EXP_BIAS, DOF_FOCAL, DOF_FSTOP, BLOOM))


def build_camera():
    """Plain CameraActor (NOT CineCamera — physical exposure crushes a dim scene
    to black in -game) looking down the channel so the blades recede into depth
    and the shaft rakes across the frame."""
    cam = _actor_subsys.spawn_actor_from_class(
        unreal.CameraActor, unreal.Vector(CAM_X, 0.0, 120.0),
        unreal.Rotator(0.0, 0.0, 0.0))               # look +X, level
    cc = cam.get_component_by_class(unreal.CameraComponent)
    if cc:
        _try_set(cc, "field_of_view", FOV)
    # Auto-activate as Player0 so a -game/packaged run renders from THIS camera.
    _try_set(cam, "auto_activate_for_player", unreal.AutoReceiveInput.PLAYER0)
    _label(cam, "Camera")
    return cam


# ---------------------------------------------------------------------------
# Motion (v2 dark-ride parallax) — LevelSequence authored in Python, auto-played
# + looped by a LevelSequenceActor so a `-game -windowed` run animates with NO
# Blueprint and NO editor tick.
# ---------------------------------------------------------------------------
#
# WHY A SEQUENCE (not a Blueprint tick): the wallpaper runs the COOKED `-game`
# build, where there is no editor and we author no Blueprints. A LevelSequenceActor
# with auto_play=True + loop_count=-1 is the one engine-native way to get a saved
# level to animate on its own at boot. The actor + its asset reference are saved
# into the .umap, so the packaged runtime replays it with zero extra code.
#
# WHY KEYFRAMES, NOT A CURVE EXPRESSION: Sequencer has no runtime sine generator;
# we BAKE a sine into discrete double-channel keys. Slow drift tolerates a coarse
# display rate (30 fps is plenty for a 41 s period), and cubic/auto tangents smooth
# the samples. The FIRST key value == LAST key value at the loop boundary so the
# infinite loop is seamless (no pop). Period is rounded to a whole number of frames
# so the loop length divides evenly.
#
# WHY SPEED IS BAKED AT AUTHOR TIME: a `-game` auto-play LevelSequence plays at a
# fixed wall-clock rate (play_rate is on the player, not trivially settable from a
# saved actor without a Blueprint). So GlobalSpeed scales the *authored periods*:
# higher speed -> shorter periods -> the same loop completes faster. SPEED==0 is the
# FREEZE seam (reduce-motion / GPU-throttle): we emit a flat single-value loop so
# the scene is byte-equivalent to the static look but the sequence machinery is
# still present (so flipping speed back on needs no re-author of the static scene).

# UE MovieScene3DTransformSection get_all_channels() order (CacheChannelProxy):
#   0,1,2 = Location  X,Y,Z
#   3,4,5 = Rotation  Roll(X), Pitch(Y), Yaw(Z)
#   6,7,8 = Scale     X,Y,Z
_CH_LOC_X, _CH_LOC_Y, _CH_LOC_Z = 0, 1, 2
_CH_ROT_ROLL, _CH_ROT_PITCH, _CH_ROT_YAW = 3, 4, 5


def _motion_fail(msg):
    """Motion is OPTIONAL: a failure here logs loudly but does NOT touch _FAIL — a
    static Indigo Channel is a complete, valid PASS. (Contrast build_fog, where a
    missing volumetric path IS a hard fail.)"""
    unreal.log_warning("[AgentOS indigo_channel] MOTION skipped: " + str(msg))


def _import_sine_keys(channel, base, amp_deg_or_cm, period_sec, fps, samples_per_period=24):
    """Bake one sine cycle of (base + amp*sin) onto a double channel as keys, over
    EXACTLY `period_sec * fps` frames so the loop divides evenly, with the LAST key
    equal to the FIRST (seamless loop). Returns the loop length in frames.

    SPEED==0 freeze: callers pass amp=0, which lays a single flat key — a held value,
    i.e. no motion, identical look to the static scene."""
    import math
    loop_frames = max(1, int(round(period_sec * fps)))
    if amp_deg_or_cm == 0.0:
        # Freeze: one key at the base value; nothing animates.
        channel.add_key(
            unreal.FrameNumber(0), float(base),
            interpolation=unreal.MovieSceneKeyInterpolation.AUTO,
        )
        return loop_frames
    n = max(2, int(samples_per_period))
    for i in range(n + 1):                       # +1 so we explicitly place the closing key
        frac = i / float(n)                       # 0..1 across the period
        frame = int(round(frac * loop_frames))
        if i == n:
            frame = loop_frames                   # closing key lands exactly on the loop end
            value = base                          # == first key (sin(2π)=0) -> seamless
        else:
            value = base + amp_deg_or_cm * math.sin(2.0 * math.pi * frac)
        channel.add_key(
            unreal.FrameNumber(frame), float(value),
            interpolation=unreal.MovieSceneKeyInterpolation.AUTO,
        )
    return loop_frames


def _add_transform_section(seq, actor, end_frame):
    """Possess `actor` in `seq`, add a 3D-transform track+section spanning
    [0, end_frame], and return (section, channels). Returns (None, None) on any
    step failing (caller treats as a soft motion failure)."""
    binding = seq.add_possessable(object_to_possess=actor)
    if binding is None:
        _motion_fail("add_possessable returned None for {}".format(actor.get_actor_label()))
        return None, None
    track = binding.add_track(unreal.MovieScene3DTransformTrack)
    if track is None:
        _motion_fail("add_track(MovieScene3DTransformTrack) returned None")
        return None, None
    section = track.add_section()
    if section is None:
        _motion_fail("add_section returned None")
        return None, None
    section.set_range(0, int(end_frame))
    channels = section.get_all_channels()
    if channels is None or len(channels) < 9:
        _motion_fail("transform section returned {} channels (<9) — wrong section type?".format(
            0 if channels is None else len(channels)))
        return None, None
    return section, channels


def _set_autoplay_loop(seq_actor):
    """Configure the LevelSequenceActor to auto-play + loop infinitely in -game.

    NOTE: `playback_settings` is exposed READ-ONLY in the Python API, but it returns
    a MUTABLE FMovieSceneSequencePlaybackSettings struct we can edit and write back.
    loop_count is a MovieSceneSequenceLoopCount WRAPPER struct (not a raw int): its
    `value` field is -1 for infinite. We try the wrapper first, then fall back to
    assigning a plain int (older bindings accepted that), then to the actor-level
    `auto_play` property if present."""
    ok = True
    try:
        settings = seq_actor.get_editor_property("playback_settings")
    except Exception as exc:  # noqa: BLE001
        _motion_fail("could not read playback_settings: {}".format(exc))
        settings = None

    if settings is not None:
        _try_set(settings, "auto_play", True)
        # loop_count: wrapper struct first.
        looped = False
        try:
            lc = settings.get_editor_property("loop_count")
            if _try_set(lc, "value", -1):
                settings.set_editor_property("loop_count", lc)
                looped = True
        except Exception as exc:  # noqa: BLE001
            log("loop_count wrapper path failed ({}); trying plain int".format(exc))
        if not looped:
            looped = _try_set(settings, "loop_count", -1)
        ok = looped and ok
        # Write the (possibly read-only-getter but mutable) struct back onto the actor.
        if not _try_set(seq_actor, "playback_settings", settings):
            # Some builds expose auto_play directly on the actor; belt-and-suspenders.
            _try_set(seq_actor, "auto_play", True)
    else:
        ok = False
        # Last resort: actor-level flags if the struct was unreadable.
        _try_set(seq_actor, "auto_play", True)

    return ok


def build_camera_motion(cam, light):
    """Author the dark-ride parallax LevelSequence + a LevelSequenceActor that
    auto-plays and loops it in -game. Strictly additive: failure here never aborts
    the static scene (see _motion_fail — it does NOT touch _FAIL).

    Camera: lateral Y dolly (±CAM_DOLLY_Y_AMP, CAM_DOLLY_Y_SEC) + tiny pitch bob
            (±CAM_PITCH_AMP deg, CAM_PITCH_SEC) on INCOMMENSURATE periods -> the near
            blades slide against the far ones = real parallax that never tiles.
    Light:  yaw breath (±LIGHT_YAW_AMP deg, LIGHT_YAW_SEC) -> the shafts barely sweep.

    GlobalSpeed (MOTION_SPEED): scales the authored periods. 0.0 -> FREEZE (flat
    held keys; look identical to static). The loop length is the longest scaled
    period rounded to whole frames, so every channel closes on its loop boundary."""
    if cam is None:
        _motion_fail("no camera actor — cannot author motion")
        return None
    if light is None:
        _motion_fail("no light actor — light breath will be skipped")

    speed = MOTION_SPEED
    if speed < 0.0:
        speed = 0.0
    freeze = (speed == 0.0)

    # Effective periods: speed scales pace, so a faster speed shortens the period.
    # speed==0 is handled as freeze (amp=0), so we don't divide by zero here.
    def _scaled(period):
        return period if freeze else (period / speed)

    cam_dolly_sec = _scaled(CAM_DOLLY_Y_SEC)
    cam_pitch_sec = _scaled(CAM_PITCH_SEC)
    light_yaw_sec = _scaled(LIGHT_YAW_SEC)

    # Loop length = the longest active period (frozen: just use the dolly period for a
    # well-formed 1-cycle span). Every per-channel cycle is authored against this same
    # span so they all close together at the loop boundary.
    loop_sec = max(cam_dolly_sec, cam_pitch_sec, light_yaw_sec)
    loop_frames = max(1, int(round(loop_sec * SEQ_FPS)))

    # --- delete-before-create the sequence asset (idempotent, scene's style) ---
    if unreal.EditorAssetLibrary.does_asset_exist(SEQ_PATH):
        unreal.EditorAssetLibrary.delete_asset(SEQ_PATH)
        log("deleted prior motion sequence {}".format(SEQ_PATH))

    seq = _assets.create_asset(SEQ_NAME, SEQ_DIR, unreal.LevelSequence,
                               unreal.LevelSequenceFactoryNew())
    if seq is None:
        _motion_fail("create_asset returned None for {}".format(SEQ_PATH))
        return None

    seq.set_display_rate(unreal.FrameRate(numerator=SEQ_FPS, denominator=1))
    seq.set_playback_start(0)
    seq.set_playback_end(int(loop_frames))

    # --- camera track: Y dolly + pitch bob (amp=0 when frozen) ---
    cam_amp_y     = 0.0 if freeze else CAM_DOLLY_Y_AMP
    cam_amp_pitch = 0.0 if freeze else CAM_PITCH_AMP
    cam_base = cam.get_actor_location()
    cam_rot  = cam.get_actor_rotation()
    sec, chans = _add_transform_section(seq, cam, loop_frames)
    if chans is not None:
        # Hold X and Z at their authored values so the loop start == the static pose.
        _import_sine_keys(chans[_CH_LOC_X], cam_base.x, 0.0, cam_dolly_sec, SEQ_FPS)
        _import_sine_keys(chans[_CH_LOC_Z], cam_base.z, 0.0, cam_dolly_sec, SEQ_FPS)
        _import_sine_keys(chans[_CH_LOC_Y], cam_base.y, cam_amp_y, cam_dolly_sec, SEQ_FPS)
        # Pitch bob on its own incommensurate period; roll/yaw held flat.
        _import_sine_keys(chans[_CH_ROT_PITCH], cam_rot.pitch, cam_amp_pitch, cam_pitch_sec, SEQ_FPS)
        log("camera motion: dolly±{}cm/{:.0f}s pitch±{}deg/{:.0f}s (freeze={})".format(
            cam_amp_y, cam_dolly_sec, cam_amp_pitch, cam_pitch_sec, freeze))

    # --- light track: yaw breath ---
    if light is not None:
        light_amp = 0.0 if freeze else LIGHT_YAW_AMP
        light_rot = light.get_actor_rotation()
        _lsec, lchans = _add_transform_section(seq, light, loop_frames)
        if lchans is not None:
            _import_sine_keys(lchans[_CH_ROT_YAW], light_rot.yaw, light_amp, light_yaw_sec, SEQ_FPS)
            log("light motion: yaw±{}deg/{:.0f}s (freeze={})".format(
                light_amp, light_yaw_sec, freeze))

    unreal.EditorAssetLibrary.save_asset(SEQ_PATH)
    log("motion sequence saved: {} ({} frames @ {}fps, speed={})".format(
        SEQ_PATH, loop_frames, SEQ_FPS, speed))

    # --- spawn the LevelSequenceActor that auto-plays + loops it in -game ---
    seq_actor = _actor_subsys.spawn_actor_from_class(
        unreal.LevelSequenceActor, unreal.Vector(0.0, 0.0, 0.0),
        unreal.Rotator(0.0, 0.0, 0.0))
    if seq_actor is None:
        _motion_fail("could not spawn LevelSequenceActor")
        return None
    # Bind the asset (set_sequence is the documented setter; level_sequence_asset
    # is read-only). Without this the actor plays nothing.
    try:
        seq_actor.set_sequence(seq)
    except Exception as exc:  # noqa: BLE001
        _motion_fail("set_sequence failed: {}".format(exc))
        _actor_subsys.destroy_actor(seq_actor)
        return None
    _set_autoplay_loop(seq_actor)
    _label(seq_actor, "MotionPlayer")
    log("LevelSequenceActor spawned (auto_play+loop) -> plays in -game")
    return seq_actor


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
    slab_mat = build_slab_material()
    build_blades(slab_mat)
    build_floor(slab_mat)
    light = build_light()
    build_fog()
    build_post()
    cam = build_camera()

    # MOTION (v2): gated OFF by default so the static-look iteration is unaffected.
    # Strictly additive — a motion failure logs but never appends to _FAIL, so the
    # static scene still reaches PASS. Authored AFTER the actors exist + BEFORE save,
    # so the LevelSequenceActor + its asset reference persist into the .umap and the
    # cooked -game build replays it with no editor and no Blueprint.
    if MOTION_ON:
        log("INDIGO_MOTION on (speed={}) — authoring dark-ride parallax sequence".format(
            MOTION_SPEED))
        try:
            build_camera_motion(cam, light)
        except Exception as exc:  # noqa: BLE001 — motion must never abort the static scene
            import traceback
            _motion_fail("build_camera_motion raised: {}".format(exc))
            for line in traceback.format_exc().splitlines():
                unreal.log_warning("[AgentOS indigo_channel] " + line)
    else:
        log("INDIGO_MOTION off — static look (set INDIGO_MOTION=1 to author motion)")

    saved = _level_subsys.save_current_level()
    log("save_current_level() -> {}".format(saved))

    if _FAIL:
        # A broken scene must NEVER reach the success marker. Log loudly + RETURN
        # (not raise) so the editor still runs the trailing `Quit` ExecCmd instead
        # of idling to the watchdog — author_scene.sh keys FAIL off the absent marker.
        for f in _FAIL:
            unreal.log_error("[AgentOS indigo_channel] BUILD FAIL: " + f)
        log("BUILD FAILED with {} issue(s) — NOT emitting success marker".format(len(_FAIL)))
        return

    # The success marker author_scene.sh greps for (pass via MARK=...).
    log("Indigo Channel scene built at {}".format(LEVEL_PATH))


def _run():
    """Top-level guard: never let an uncaught exception leave the headless editor
    idling to the watchdog. Log the traceback, then fall through so the trailing
    `Quit` ExecCmd fires. FAIL is signalled by the absent marker."""
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        import traceback
        unreal.log_error("[AgentOS indigo_channel] FATAL: {}".format(exc))
        for line in traceback.format_exc().splitlines():
            unreal.log_error("[AgentOS indigo_channel] " + line)


# Runs under `__main__`, `py <path>`, and exec(open(...).read()) alike.
_run()
