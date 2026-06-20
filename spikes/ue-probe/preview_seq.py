# preview_seq.py — capture a SEQUENCE of stills of CalmWallpaper from
# AgentOS_Camera as the wave advances, for assembling into a motion clip.
# Same no-RE approach as preview_shot.py, but instead of one HighResShot it
# converges, then fires a shot every CAPTURE_EVERY ticks for FRAMES frames.
# Drive at editor launch:
#   UnrealEditor <proj> /Game/AgentOS/CalmWallpaper -RenderOffscreen -unattended \
#     -ResX=960 -ResY=540 -stdout -FullStdOutLogOutput -ExecCmds=py /abs/preview_seq.py
# preview_motion.sh waits for the 'DONE preview_seq' marker, then ffmpegs the
# numbered PNGs in Saved/Screenshots/<Platform>/ into an mp4 + a filmstrip.

import os

import unreal

CAM_LABEL = "AgentOS_Camera"
RES = os.environ.get("SEQ_RES", "960x540")
CONVERGE_TICKS = int(os.environ.get("SEQ_CONVERGE", "45"))   # settle exposure/Lumen
FRAMES = int(os.environ.get("SEQ_FRAMES", "80"))             # frames to capture
CAPTURE_EVERY = int(os.environ.get("SEQ_EVERY", "2"))        # ticks between shots

_eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
_ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
_les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)


def _log(m):
    unreal.log("[preview_seq] " + str(m))


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
    _log("viewport -> {}".format(CAM_LABEL))
else:
    _log("WARN: {} not found; default viewport".format(CAM_LABEL))

try:
    _les.editor_set_game_view(True)
except Exception as exc:  # noqa: BLE001
    _log("game_view skip: {}".format(exc))

_state = {"n": 0, "shots": 0, "done": False}


def _on_tick(_delta):
    _state["n"] += 1
    n = _state["n"]
    if n < CONVERGE_TICKS or _state["done"]:
        return
    if (n - CONVERGE_TICKS) % CAPTURE_EVERY == 0:
        # re-assert camera (game-view toggle can nudge it) then shoot.
        if _cam:
            _ues.set_level_viewport_camera_info(
                _cam.get_actor_location(), _cam.get_actor_rotation())
        world = _ues.get_editor_world()
        unreal.SystemLibrary.execute_console_command(world, "HighResShot " + RES)
        _state["shots"] += 1
        if _state["shots"] % 16 == 0:
            _log("captured {}/{}".format(_state["shots"], FRAMES))
        if _state["shots"] >= FRAMES:
            _state["done"] = True
            _log("DONE preview_seq captured {} frames".format(_state["shots"]))
            try:
                unreal.unregister_slate_post_tick_callback(_handle)
            except Exception:
                pass


_handle = unreal.register_slate_post_tick_callback(_on_tick)
_log("seq tick registered (converge={} frames={} every={} res={})".format(
    CONVERGE_TICKS, FRAMES, CAPTURE_EVERY, RES))
