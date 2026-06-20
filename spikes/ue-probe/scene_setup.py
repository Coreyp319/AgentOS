# scene_setup.py — build a calm "wallpaper" Lumen scene inside UnrealEditor.
#
# WHAT THIS DOES
#   Spawns a representative, low-motion Lumen scene we can measure as a candidate
#   desktop-wallpaper resident:
#     - ground plane (scaled BasicShapes/Plane)
#     - a handful of primitive static meshes (cubes + spheres as stand-in
#       terrain/structures)
#     - a DirectionalLight at a LOW golden-hour angle (warm, ~6 deg above horizon)
#     - SkyAtmosphere + SkyLight (captures the atmosphere) + ExponentialHeightFog
#     - a PostProcessVolume (unbound/infinite-extent) that pins
#       DynamicGlobalIlluminationMethod = Lumen and ReflectionMethod = Lumen ON,
#       independent of project defaults
#     - a CineCameraActor framing the scene
#   Idempotent: every actor it creates is labelled with the AGENTOS_PREFIX; a
#   re-run destroys the prior AgentOS-prefixed actors first, so you can iterate.
#
# HOW TO RUN IT (pick one)
#   A) At editor launch, headless (this is how the main agent drives it):
#        UnrealEditor <uproject> -RenderOffscreen -unattended -stdout \
#          -FullStdOutLogOutput \
#          -ExecCmds=py /home/corey/Documents/AgentOS/spikes/ue-probe/scene_setup.py
#      *** DO NOT add inner quotes around the path. *** UE re-quotes the whole
#      -ExecCmds VALUE itself; inner quotes collide with that and FParse ends up
#      reading just `py ` (path lost) — the log shows `Cmd: py ` empty and the
#      editor idles forever (this cost a session + an 8h zombie). The path has no
#      spaces, so no quotes are needed. Easiest: use `author_scene.sh`, which
#      builds this correctly and adds a watchdog + VRAM pre-flight.
#
#   B) From the in-editor Python console (Window > Output Log, switch the dropdown
#      to "Python"), or Tools > Execute Python Script:
#        exec(open("/home/corey/Documents/AgentOS/spikes/ue-probe/scene_setup.py").read())
#
#   After it runs it saves the level to LEVEL_PATH (default /Game/AgentOS/CalmWallpaper).
#   To launch *into* that map for a measurement run, pass it as the map arg:
#        launch_offscreen.sh --map /Game/AgentOS/CalmWallpaper --exec "<cvar rung>"
#
# API NOTES (verified against UE 5.6/5.8 Python API — surface is stable in 5.8):
#   - Actors are spawned via the EditorActorSubsystem (the EditorLevelLibrary
#     free functions are deprecated in 5.x but still work; we use the subsystem):
#       subsys = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
#       actor  = subsys.spawn_actor_from_class(cls, location, rotation)
#       subsys.destroy_actor(actor)
#       subsys.get_all_level_actors() -> Array[Actor]
#       spawn_actor_from_class(actor_class, location, rotation=[0,0,0], transient=False)
#   - New level + save go through the LevelEditorSubsystem:
#       les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
#       les.new_level(asset_path, is_partitioned_world=False) -> bool
#       les.load_level(asset_path) -> bool
#       les.save_current_level() -> bool
#   - Engine primitive meshes live at /Engine/BasicShapes/{Cube,Sphere,Plane,...}
#     loaded via unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cube.Cube").
#   - Lumen is pinned ON via a PostProcessVolume's settings:
#       DynamicGlobalIlluminationMethod = unreal.DynamicGlobalIlluminationMethod.LUMEN
#       ReflectionMethod                = unreal.ReflectionMethod.LUMEN
#     (equivalent to r.DynamicGlobalIlluminationMethod=1 / r.ReflectionMethod=1).
#
# Sources (verified 2026-06-19):
#   EditorActorSubsystem — dev.epicgames.com/.../python-api/class/EditorActorSubsystem (5.6)
#   LevelEditorSubsystem — dev.epicgames.com/.../python-api/class/LevelEditorSubsystem (5.6)
#   StaticMeshActor      — dev.epicgames.com/.../python-api/class/StaticMeshActor (5.6)
#   Lumen GI & Reflections — dev.epicgames.com/documentation/unreal-engine/
#                            lumen-global-illumination-and-reflections-in-unreal-engine (5.8)

import unreal

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
AGENTOS_PREFIX = "AgentOS_"               # every actor we own is labelled with this
LEVEL_PATH = "/Game/AgentOS/CalmWallpaper"  # where the level gets saved

CUBE_ASSET   = "/Engine/BasicShapes/Cube.Cube"
SPHERE_ASSET = "/Engine/BasicShapes/Sphere.Sphere"
PLANE_ASSET  = "/Engine/BasicShapes/Plane.Plane"

# ---------------------------------------------------------------------------
# Subsystems / helpers
# ---------------------------------------------------------------------------
_actor_subsys = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
_level_subsys = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)


def log(msg):
    unreal.log("[AgentOS scene_setup] " + str(msg))


def _label(actor, name):
    """Tag an actor so re-runs can find and clear it."""
    actor.set_actor_label(AGENTOS_PREFIX + name)
    return actor


def clear_prior():
    """Destroy any actor previously created by this script (idempotency)."""
    removed = 0
    for actor in _actor_subsys.get_all_level_actors():
        try:
            if actor.get_actor_label().startswith(AGENTOS_PREFIX):
                _actor_subsys.destroy_actor(actor)
                removed += 1
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            log("skip actor during clear: {}".format(exc))
    log("cleared {} prior AgentOS actor(s)".format(removed))


def spawn_static_mesh(name, asset_path, location, rotation=None, scale=None):
    rotation = rotation or unreal.Rotator(0.0, 0.0, 0.0)
    mesh = unreal.EditorAssetLibrary.load_asset(asset_path)
    actor = _actor_subsys.spawn_actor_from_class(
        unreal.StaticMeshActor, location, rotation
    )
    actor.static_mesh_component.set_static_mesh(mesh)
    if scale is not None:
        actor.set_actor_scale3d(scale)
    return _label(actor, name)


def spawn(name, cls, location, rotation=None):
    rotation = rotation or unreal.Rotator(0.0, 0.0, 0.0)
    actor = _actor_subsys.spawn_actor_from_class(cls, location, rotation)
    return _label(actor, name)


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------
def build_ground():
    # BasicShapes/Plane is 100x100 uu; scale to a generous 80m x 80m pad.
    spawn_static_mesh(
        "Ground",
        PLANE_ASSET,
        unreal.Vector(0.0, 0.0, 0.0),
        scale=unreal.Vector(80.0, 80.0, 1.0),
    )


def build_structures():
    # A small, calm arrangement of cubes (structures) and spheres (boulders).
    cubes = [
        ("Cube_A", unreal.Vector(300.0, 0.0, 150.0),    unreal.Vector(3.0, 3.0, 3.0)),
        ("Cube_B", unreal.Vector(-450.0, 250.0, 100.0), unreal.Vector(2.0, 2.0, 2.0)),
        ("Cube_C", unreal.Vector(150.0, -500.0, 250.0), unreal.Vector(5.0, 2.0, 5.0)),
        ("Cube_D", unreal.Vector(-200.0, -300.0, 75.0), unreal.Vector(1.5, 6.0, 1.5)),
    ]
    for name, loc, scl in cubes:
        spawn_static_mesh(name, CUBE_ASSET, loc, scale=scl)

    spheres = [
        ("Sphere_A", unreal.Vector(600.0, 400.0, 120.0),  unreal.Vector(2.4, 2.4, 2.4)),
        ("Sphere_B", unreal.Vector(-650.0, -150.0, 90.0), unreal.Vector(1.8, 1.8, 1.8)),
        ("Sphere_C", unreal.Vector(50.0, 700.0, 100.0),   unreal.Vector(2.0, 2.0, 2.0)),
    ]
    for name, loc, scl in spheres:
        spawn_static_mesh(name, SPHERE_ASSET, loc, scale=scl)


def build_lighting():
    # Directional light at a LOW golden-hour angle. Pitch ~ -6 deg below the sun
    # vector => sun sits just above the horizon; warm tint, gentle intensity.
    sun = spawn(
        "SunLight",
        unreal.DirectionalLight,
        unreal.Vector(0.0, 0.0, 1000.0),
        unreal.Rotator(0.0, -6.0, 35.0),  # (roll, pitch, yaw): low pitch = golden hour
    )
    # NOTE: unreal.DirectionalLight has no `directional_light_component` attribute
    # in the UE 5.8 Python API — resolve the component by class (robust, matches
    # how the SkyLight/Fog actors below are handled).
    comp = sun.get_component_by_class(unreal.DirectionalLightComponent)
    if comp:
        comp.set_intensity(4.0)  # lux-ish; low, warm dusk read
        comp.set_light_color(unreal.LinearColor(1.0, 0.72, 0.42, 1.0))
        try:
            comp.set_editor_property("atmosphere_sun_light", True)  # drive SkyAtmosphere
        except Exception as exc:  # noqa: BLE001
            log("atmosphere_sun_light set skipped: {}".format(exc))
    else:
        log("WARN: no DirectionalLightComponent on sun actor; sun config skipped")

    # Atmosphere + ambient sky + fog: the calm, volumetric backdrop.
    spawn("SkyAtmosphere", unreal.SkyAtmosphere, unreal.Vector(0.0, 0.0, 0.0))

    skylight = spawn("SkyLight", unreal.SkyLight, unreal.Vector(0.0, 0.0, 200.0))
    sky_comp = skylight.get_component_by_class(unreal.SkyLightComponent)
    if sky_comp:
        # Real-time capture so the SkyLight reflects the SkyAtmosphere we just made.
        sky_comp.set_editor_property("real_time_capture", True)
        # SkyLightComponent derives from LightComponentBase (no set_intensity()); set
        # the property directly so this can't raise like the directional light did.
        sky_comp.set_editor_property("intensity", 1.0)

    fog = spawn(
        "HeightFog", unreal.ExponentialHeightFog, unreal.Vector(0.0, 0.0, 0.0)
    )
    fog_comp = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
    if fog_comp:
        fog_comp.set_editor_property("fog_density", 0.02)
        fog_comp.set_editor_property("fog_height_falloff", 0.2)


def build_lumen_postprocess():
    """Pin Lumen GI + Lumen Reflections ON via an unbound PostProcessVolume.

    This makes the scene's lighting method explicit and independent of project
    defaults — exactly the FULL/native rung the cvar ladder throttles down from.
    """
    ppv = spawn("LumenPPV", unreal.PostProcessVolume, unreal.Vector(0.0, 0.0, 0.0))
    ppv.set_editor_property("unbound", True)  # infinite extent — affects whole level

    settings = ppv.get_editor_property("settings")  # FPostProcessSettings

    # Lumen GI
    settings.set_editor_property("override_dynamic_global_illumination_method", True)
    settings.set_editor_property(
        "dynamic_global_illumination_method",
        unreal.DynamicGlobalIlluminationMethod.LUMEN,
    )
    # Lumen Reflections
    settings.set_editor_property("override_reflection_method", True)
    settings.set_editor_property(
        "reflection_method", unreal.ReflectionMethod.LUMEN
    )

    ppv.set_editor_property("settings", settings)
    log("Lumen GI + Reflections pinned ON via PostProcessVolume")


def build_camera():
    cam = spawn(
        "Camera",
        unreal.CineCameraActor,
        unreal.Vector(-1400.0, -1400.0, 700.0),
        unreal.Rotator(0.0, -18.0, 45.0),  # look down-and-across the scene
    )
    return cam


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    # Create/load the destination level so the scene lands somewhere persistent.
    # new_level on an existing path will fail; fall back to load_level then clear.
    if unreal.EditorAssetLibrary.does_asset_exist(LEVEL_PATH):
        _level_subsys.load_level(LEVEL_PATH)
        log("loaded existing level {}".format(LEVEL_PATH))
    else:
        created = _level_subsys.new_level(LEVEL_PATH)
        log("new_level({}) -> {}".format(LEVEL_PATH, created))

    clear_prior()

    build_ground()
    build_structures()
    build_lighting()
    build_lumen_postprocess()
    build_camera()

    saved = _level_subsys.save_current_level()
    log("save_current_level() -> {}".format(saved))
    log("DONE — calm Lumen wallpaper scene built at {}".format(LEVEL_PATH))


if __name__ == "__main__":
    main()
else:
    # Also run when invoked via `py "<path>"` or exec(open(...).read()),
    # which import the module under a non-__main__ name.
    main()
