# pf_game_diag.py — maximal-signal -game probe for the "PrismField renders black" bug.
# Runs in the REAL -game world. Editor subsystems are None in -game, so we get the
# world handle by FINDING the loaded UWorld object by path (PFDIAG_WORLD env), then use
# runtime GameplayStatics. Logs (all tagged [PFDIAG], one-grep cleanup):
#   1. actor count + every actor's class/location  (H1: actors in world?)
#   2. Player0 view target + controlled pawn + camera loc/rot (H2: view aimed at scene?)
#   3. a screenshot via AutomationLibrary + console HighResShot (H3: what renders)
#
#   PFDIAG_WORLD=/Game/AgentOS/PrismField.PrismField \
#   UnrealEditor <proj> /Game/AgentOS/PrismField -game -RenderOffscreen -unattended \
#     -stdout -FullStdOutLogOutput -ExecCmds="py /abs/pf_game_diag.py"
import os
import unreal

RES_X, RES_Y = 1280, 720
CONVERGE_TICKS = 260   # offscreen ticks fly (~100/s); ~2.5s for Lumen to settle
FLUSH_TICKS = 220      # keep ticking so the PNG flushes before we quit
WORLD_PATH = os.environ.get("PFDIAG_WORLD", "/Game/AgentOS/PrismField.PrismField")
SHOT_NAME = os.environ.get("PFDIAG_SHOT", "PFDIAG_shot")


def _log(m):
    unreal.log("[PFDIAG] " + str(m))


def _world():
    """Get the live -game UWorld without editor subsystems: find the loaded UWorld
    object by package path, fall back to chaining get_world() off it."""
    for getter in (
        lambda: unreal.find_object(None, WORLD_PATH),
        lambda: unreal.load_object(None, WORLD_PATH),
    ):
        try:
            o = getter()
            if o is not None:
                w = o if isinstance(o, unreal.World) else o.get_world()
                if w is not None:
                    return w
        except Exception as exc:  # noqa: BLE001
            _log("world getter failed: {}".format(exc))
    return None


def _dump(world):
    try:
        actors = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor)
        _log("ACTOR COUNT in -game world = {}".format(len(actors)))
        for a in actors:
            try:
                loc = a.get_actor_location()
                try:
                    lbl = a.get_actor_label()
                except Exception:
                    lbl = a.get_name()
                _log("  actor {} :: {} @ ({:.0f},{:.0f},{:.0f})".format(
                    lbl, a.get_class().get_name(), loc.x, loc.y, loc.z))
            except Exception as exc:  # noqa: BLE001
                _log("  actor dump error: {}".format(exc))
    except Exception as exc:  # noqa: BLE001
        _log("get_all_actors_of_class FAILED: {}".format(exc))

    try:
        pc = unreal.GameplayStatics.get_player_controller(world, 0)
        _log("PlayerController = {}".format(pc.get_name() if pc else None))
        if pc:
            try:
                vt = pc.get_view_target()
                _log("VIEW TARGET = {} :: {} @ {}".format(
                    vt.get_name() if vt else None,
                    vt.get_class().get_name() if vt else None,
                    vt.get_actor_location() if vt else None))
            except Exception as exc:  # noqa: BLE001
                _log("get_view_target failed: {}".format(exc))
            try:
                pawn = pc.get_controlled_pawn()
                _log("CONTROLLED PAWN = {} :: {} @ {}".format(
                    pawn.get_name() if pawn else None,
                    pawn.get_class().get_name() if pawn else None,
                    pawn.get_actor_location() if pawn else None))
            except Exception as exc:  # noqa: BLE001
                _log("get_controlled_pawn failed: {}".format(exc))
    except Exception as exc:  # noqa: BLE001
        _log("player controller probe FAILED: {}".format(exc))

    try:
        pcm = unreal.GameplayStatics.get_player_camera_manager(world, 0)
        if pcm:
            cl = pcm.get_camera_location()
            cr = pcm.get_camera_rotation()
            _log("CAMERA loc=({:.0f},{:.0f},{:.0f}) rot=(p={:.1f},y={:.1f},r={:.1f})".format(
                cl.x, cl.y, cl.z, cr.pitch, cr.yaw, cr.roll))
        else:
            _log("NO PlayerCameraManager")
    except Exception as exc:  # noqa: BLE001
        _log("camera manager probe FAILED: {}".format(exc))


def _shoot(world):
    # Path 1: AutomationLibrary (routes to the game viewport; writes under Saved/Screenshots).
    try:
        unreal.AutomationLibrary.take_high_res_screenshot(RES_X, RES_Y, SHOT_NAME)
        _log("AutomationLibrary.take_high_res_screenshot('{}') issued".format(SHOT_NAME))
    except Exception as exc:  # noqa: BLE001
        _log("AutomationLibrary screenshot FAILED: {}".format(exc))
    # Path 2: console HighResShot with a VALID world context.
    try:
        unreal.SystemLibrary.execute_console_command(world, "HighResShot {}x{}".format(RES_X, RES_Y))
        _log("console HighResShot issued (world={})".format(world is not None))
    except Exception as exc:  # noqa: BLE001
        _log("console HighResShot FAILED: {}".format(exc))


_state = {"n": 0, "done": False}


def _on_tick(_delta):
    _state["n"] += 1
    n = _state["n"]
    if not _state["done"] and n >= CONVERGE_TICKS:
        world = _world()
        _log("==== DIAG at tick {} (world={}) ====".format(n, world))
        if world is not None:
            _dump(world)
        else:
            _log("WORLD HANDLE STILL NONE — dump skipped")
        _shoot(world)
        _state["done"] = True
    elif _state["done"] and n >= CONVERGE_TICKS + FLUSH_TICKS:
        _log("==== DIAG DONE (tick {}) ====".format(n))
        try:
            unreal.unregister_slate_post_tick_callback(_handle)
        except Exception:
            pass
        try:
            unreal.SystemLibrary.execute_console_command(_world(), "quit")
        except Exception:
            pass


_handle = unreal.register_slate_post_tick_callback(_on_tick)
_log("tick callback registered (world_path={}, converge={})".format(WORLD_PATH, CONVERGE_TICKS))
