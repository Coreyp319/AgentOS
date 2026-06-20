# indigo_channel_setup.py — author "The Indigo Channel" dark-ride tableau.
#
# WHAT THIS IS (ADR-0023, the DIMENSIONAL pivot — supersedes the flat
# gradient_wave_setup.py "shader on a wall", which the user rejected because
# nothing in it needed Unreal: a 2D shader did it cheaper.)
#
#   A volumetric-fog ROOM, not a wall. One cool CYAN directional light rakes in
#   diagonally from the upper-left (this is 00090's focal glow, now an actual
#   light in 3-space). Its shaft is caught in thin cool fog and CUT by a few
#   dark monolith "blades" receding into depth — the blades carve the light into
#   real god-rays and give genuine parallax; the fog is the indigo->violet body
#   the cyan blooms through. Depth-of-field is REAL Cinematic DoF (blurs by world
#   distance), NOT the analytic shimmer-sparkle whose bokeh got rejected.
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
# TUNE WITHOUT EDITING CODE (env knobs — the user liked this from the last scene):
#   INDIGO_EXP_BIAS      manual exposure stops      (default 10.0 — the keeper)
#   INDIGO_LIGHT_INT     directional light lux       (default 10.0)
#   INDIGO_LIGHT_PITCH   light downward tilt deg     (default -35.0)
#   INDIGO_LIGHT_YAW     light horizontal dir deg    (default  50.0)
#   INDIGO_VOL_SCATTER   god-ray strength            (default  4.0)
#   INDIGO_FOG_DENSITY   fog thickness (keep LOW)    (default  0.03)
#   INDIGO_DOF_FOCAL     focal distance cm           (default 2700.0 -> mid blades)
#   INDIGO_DOF_FSTOP     aperture (low = shallow)    (default  2.0)
#   INDIGO_BLOOM         bloom intensity             (default  0.40)
#   INDIGO_CAM_X         camera X (behind near blade)(default -2500.0)
#   INDIGO_FOV           camera FOV deg              (default 75.0)
#
# MOTION: v1 is static (validates the DEPTH first). The slow camera parallax
#   dolly + light-breath is v2, built + tested LIVE once the card is free — we do
#   not ship untested LevelSequence keyframe code blind.

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
LIGHT_COLOR = unreal.Color(111, 200, 222, 255)
# Volumetric fog albedo: cool, keeps inscatter in the cyan-violet family (FColor).
FOG_ALBEDO = unreal.Color(201, 224, 242, 255)

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
EXP_BIAS     = float(os.environ.get("INDIGO_EXP_BIAS",    "10.0"))
LIGHT_INT    = float(os.environ.get("INDIGO_LIGHT_INT",   "10.0"))
LIGHT_PITCH  = float(os.environ.get("INDIGO_LIGHT_PITCH", "-35.0"))
LIGHT_YAW    = float(os.environ.get("INDIGO_LIGHT_YAW",   "50.0"))
VOL_SCATTER  = float(os.environ.get("INDIGO_VOL_SCATTER", "4.0"))
FOG_DENSITY  = float(os.environ.get("INDIGO_FOG_DENSITY", "0.03"))
DOF_FOCAL    = float(os.environ.get("INDIGO_DOF_FOCAL",   "2700.0"))
DOF_FSTOP    = float(os.environ.get("INDIGO_DOF_FSTOP",   "2.0"))
BLOOM        = float(os.environ.get("INDIGO_BLOOM",       "0.40"))
CAM_X        = float(os.environ.get("INDIGO_CAM_X",       "-2500.0"))
FOV          = float(os.environ.get("INDIGO_FOV",         "75.0"))

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


def clear_prior():
    """Destroy any actor this script (or its predecessors) created."""
    removed = 0
    for actor in _actor_subsys.get_all_level_actors():
        try:
            if actor.get_actor_label().startswith(AGENTOS_PREFIX):
                _actor_subsys.destroy_actor(actor)
                removed += 1
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            log("skip actor during clear: {}".format(exc))
    log("cleared {} prior AgentOS actor(s)".format(removed))


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
    # Movable: volumetric-fog shafts need a shadow-casting movable/stationary light.
    _try_set(comp, "mobility", unreal.ComponentMobility.MOVABLE)
    _try_set(comp, "intensity", LIGHT_INT)
    _try_set(comp, "light_color", LIGHT_COLOR)
    # god-ray strength (low fog + high scatter = strong cheap shafts).
    _try_set(comp, "volumetric_scattering_intensity", VOL_SCATTER)
    # blades carve the shaft only if the light casts a volumetric shadow.
    _try_set_first(comp, ("cast_volumetric_shadow", "b_cast_volumetric_shadow"), True)
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
    build_light()
    build_fog()
    build_post()
    build_camera()

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
