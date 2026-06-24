"""fix_camera_aspect.py — ADR-0029 §A wallpaper polish.

The CalmWallpaper camera renders pillarboxed (16:9 with black bars) inside the
3440x1440 (21:9) ultrawide wallpaper window: a plain CameraActor still ends up
constraining aspect once it auto-activates for Player0, so a -game run letterboxes
to the camera's AspectRatio instead of filling the backbuffer.

This loads the EXISTING saved level (no full rebuild — preserves the approved "A"
look), flips every camera's CameraComponent to NOT constrain aspect and sets the
aspect to the ultrawide ratio, then saves. Run via author_scene.sh:

    SCENE_SCRIPT=fix_camera_aspect.py MARK='camera aspect fixed' \
      MIN_FREE_MIB=8000 bash spikes/ue-probe/author_scene.sh

(MIN_FREE_MIB is lowered from the 18000 authoring default because this is a warm
load+save of cached shaders, not a cold scene build — no ~22 GB Lumen spike.)
"""
import unreal

LEVEL_PATH    = "/Game/AgentOS/CalmWallpaper"
TARGET_ASPECT = 3440.0 / 1440.0  # DP-1 ultrawide = 2.38889

_actor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
_level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)


def log(m):
    unreal.log("AgentOS fix_camera_aspect: " + m)


if not unreal.EditorAssetLibrary.does_asset_exist(LEVEL_PATH):
    unreal.log_error("AgentOS fix_camera_aspect: level missing: " + LEVEL_PATH)
else:
    _level.load_level(LEVEL_PATH)
    cams = [a for a in _actor.get_all_level_actors()
            if isinstance(a, unreal.CameraActor) or a.get_actor_label() == "Camera"]
    log("found {} camera actor(s)".format(len(cams)))

    changed = 0
    for cam in cams:
        cc = cam.get_component_by_class(unreal.CameraComponent)
        if not cc:
            log("  '{}' has no CameraComponent — skipped".format(cam.get_actor_label()))
            continue
        before_con = cc.get_editor_property("constrain_aspect_ratio")
        before_asp = cc.get_editor_property("aspect_ratio")
        before_fov = cc.get_editor_property("field_of_view")
        log("  BEFORE '{}': constrain={} aspect={:.4f} fov={}".format(
            cam.get_actor_label(), before_con, before_asp, before_fov))
        cc.set_editor_property("constrain_aspect_ratio", False)
        cc.set_editor_property("aspect_ratio", TARGET_ASPECT)
        log("  AFTER  '{}': constrain={} aspect={:.4f}".format(
            cam.get_actor_label(),
            cc.get_editor_property("constrain_aspect_ratio"),
            cc.get_editor_property("aspect_ratio")))
        changed += 1

    saved = bool(_level.save_current_level())
    log("save_current_level() -> {}; cameras_changed={}".format(saved, changed))
    if changed > 0 and saved:
        log("camera aspect fixed")  # success MARK author_scene.sh greps for
    else:
        unreal.log_error("AgentOS fix_camera_aspect: nothing changed or save failed")
