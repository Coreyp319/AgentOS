# game_shot.py — render a TRUE -game still of CalmWallpaper, so the screenshot
# reflects the REAL runtime exposure (the PostProcessVolume AEM_MANUAL bias), NOT
# the editor viewport's own EV100 override.
#
# WHY THIS EXISTS (the bug preview_shot.py cannot catch):
#   preview_shot.py drives the EDITOR's perspective viewport (set_level_viewport_
#   camera_info + editor_set_game_view(True) + HighResShot). The editor viewport
#   applies its OWN exposure unless "Game Settings" is on — editor_set_game_view()
#   hides gizmos but does NOT force the viewport to honour the level's PostProcess
#   exposure. So a preview can look correctly-exposed while the shipped -game
#   wallpaper is black/blown, or vice-versa. For EXPOSURE truth we must render the
#   real game view through the AgentOS_Camera (auto_activate PLAYER0) under -game.
#
# HOW IT'S DRIVEN (mirror of preview_shot.py, but for -game):
#   UnrealEditor <proj> /Game/AgentOS/CalmWallpaper -game -RenderOffscreen \
#     -unattended -stdout -FullStdOutLogOutput -ExecCmds=py /abs/game_shot.py
#   (-game makes the editor binary run the actual game world + Player0 camera, so
#    the PPV exposure that ships is exactly what renders here.)
#
# Approach: register a Slate post-tick callback (NO editor-subsystem viewport
# poke — there is no editor viewport in -game). Let Lumen converge, then issue
# `HighResShot <res>` against the GAME world. In -game the PNG lands in
#   <project>/Saved/Screenshots/LinuxGame/HighresScreenshot*.png
# (LinuxGame, not LinuxEditor — that directory difference is itself the proof you
#  rendered the real runtime path, not the editor.)

import unreal

RES = "1920x1080"
CONVERGE_TICKS = 120   # let Lumen GI/reflections + auto-nothing settle (manual exp)
FLUSH_TICKS = 90       # keep ticking after the shot so the PNG flushes to disk


def _log(m):
    unreal.log("[game_shot] " + str(m))


def _game_world():
    """Get the live PIE/-game world (NOT the editor world). In a -game run the
    game world is what Player0 + the PPV exposure render through."""
    # GameplayStatics needs a world context; the editor-subsystem editor world is
    # wrong here. Walk the engine worlds and pick the one of type Game/PIE.
    try:
        worlds = unreal.EditorLevelLibrary.get_editor_world()
    except Exception:
        worlds = None
    # In -game the cleanest handle is the world owning the player; fall back to the
    # editor-subsystem world only if that fails (it still routes the console cmd to
    # the active viewport's world under -game).
    try:
        ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        w = ues.get_game_world()
        if w:
            return w
    except Exception:
        pass
    return worlds


_state = {"n": 0, "shot": False}


def _on_tick(_delta):
    _state["n"] += 1
    n = _state["n"]
    if not _state["shot"] and n >= CONVERGE_TICKS:
        world = _game_world()
        if world is None:
            _log("WARN: no game world; routing HighResShot via None context")
        # HighResShot renders the ACTIVE game viewport = Player0 = AgentOS_Camera,
        # through the real shipped PostProcessVolume exposure. This is the whole
        # point: no editor-viewport exposure override is in play.
        unreal.SystemLibrary.execute_console_command(world, "HighResShot " + RES)
        _state["shot"] = True
        _log("HighResShot {} issued (tick {}) via -game player view".format(RES, n))
    elif _state["shot"] and n >= CONVERGE_TICKS + FLUSH_TICKS:
        _log("DONE game_shot (tick {})".format(n))
        try:
            unreal.unregister_slate_post_tick_callback(_handle)
        except Exception:
            pass


_handle = unreal.register_slate_post_tick_callback(_on_tick)
_log("tick callback registered (-game; converge={} ticks, manual-exposure honoured)".format(
    CONVERGE_TICKS))
