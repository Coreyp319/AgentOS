# preview_shot.py — render a clean still of the CalmWallpaper scene from the
# AgentOS_Camera, headlessly, with NO Remote Execution dependency.
#
# Driven at editor launch:
#   UnrealEditor <proj> /Game/AgentOS/CalmWallpaper -RenderOffscreen -unattended \
#     -stdout -FullStdOutLogOutput -ExecCmds=py /abs/preview_shot.py
# (The launcher keeps the editor up ~25s so the shot renders + flushes, then kills it.)
#
# Approach: point the level-editor perspective viewport at the CineCamera's
# transform, enable game view (hide editor gizmos/grid), let Lumen converge a few
# frames via a Slate post-tick counter, then issue `HighResShot`. The PNG lands in
#   <project>/Saved/Screenshots/<Platform>/HighresScreenshot*.png

import unreal

CAM_LABEL = "AgentOS_Camera"
RES = "1920x1080"
CONVERGE_TICKS = 120   # let Lumen GI/reflections settle before the shot
FLUSH_TICKS = 90       # keep ticking after the shot so the PNG flushes to disk

_eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
_ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
_les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)


def _log(m):
    unreal.log("[preview] " + str(m))


def _find_cam():
    for a in _eas.get_all_level_actors():
        try:
            if a.get_actor_label() == CAM_LABEL:
                return a
        except Exception:
            pass
    return None


_cam = _find_cam()
if _cam:
    _ues.set_level_viewport_camera_info(_cam.get_actor_location(), _cam.get_actor_rotation())
    _log("viewport -> {} loc={} rot={}".format(
        CAM_LABEL, _cam.get_actor_location(), _cam.get_actor_rotation()))
else:
    _log("WARN: {} not found; shooting from default viewport".format(CAM_LABEL))

try:
    _les.editor_set_game_view(True)
    _log("game view ON")
except Exception as exc:  # noqa: BLE001
    _log("game_view skip: {}".format(exc))

_state = {"n": 0, "shot": False}


def _on_tick(_delta):
    _state["n"] += 1
    n = _state["n"]
    if not _state["shot"] and n >= CONVERGE_TICKS:
        # re-assert the camera (game-view toggle can nudge the viewport) then shoot.
        if _cam:
            _ues.set_level_viewport_camera_info(
                _cam.get_actor_location(), _cam.get_actor_rotation())
        world = _ues.get_editor_world()
        unreal.SystemLibrary.execute_console_command(world, "HighResShot " + RES)
        _state["shot"] = True
        _log("HighResShot {} issued (tick {})".format(RES, n))
    elif _state["shot"] and n >= CONVERGE_TICKS + FLUSH_TICKS:
        _log("DONE preview_shot (tick {})".format(n))
        try:
            unreal.unregister_slate_post_tick_callback(_handle)
        except Exception:
            pass


_handle = unreal.register_slate_post_tick_callback(_on_tick)
_log("tick callback registered (converge={} ticks)".format(CONVERGE_TICKS))
