"""make_wallpaper_gamemode.py — ADR-0029 §A wallpaper polish (cursor visibility).

A -game UnrealEditor run HIDES the OS cursor whenever the pointer is over its
viewport, because the default PlayerController has bShowMouseCursor=False. On a
full-screen wallpaper that viewport is the whole desktop, so the cursor vanishes.
NoCapture/DoNotLock (DefaultInput.ini) already stopped the cursor *lock*; this
stops the *hide* by giving the run a PlayerController whose CDO shows the cursor.

Creates:
  /Game/AgentOS/BP_WallpaperPC  (PlayerController, show_mouse_cursor=True)
  /Game/AgentOS/BP_WallpaperGM  (GameModeBase, player_controller_class=BP_WallpaperPC)

Wire-up (caller does this, once): DefaultEngine.ini ->
  [/Script/EngineSettings.GameMapsSettings]
  GlobalDefaultGameMode=/Game/AgentOS/BP_WallpaperGM.BP_WallpaperGM_C

Run via author_scene.sh:
  SCENE_SCRIPT=make_wallpaper_gamemode.py MARK='wallpaper gamemode ready' \
    MIN_FREE_MIB=8000 bash spikes/ue-probe/author_scene.sh
"""
import unreal

PKG = "/Game/AgentOS"
_tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(m):
    unreal.log("AgentOS make_wallpaper_gamemode: " + m)


def _make_bp(name, parent):
    path = "{}/{}".format(PKG, name)
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
    fac = unreal.BlueprintFactory()
    fac.set_editor_property("parent_class", parent)
    bp = _tools.create_asset(name, PKG, None, fac)
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    return bp


# --- PlayerController that shows the cursor --------------------------------
pc_bp = _make_bp("BP_WallpaperPC", unreal.PlayerController)
pc_cls = pc_bp.generated_class()
pc_cdo = unreal.get_default_object(pc_cls)
pc_cdo.set_editor_property("show_mouse_cursor", True)
unreal.BlueprintEditorLibrary.compile_blueprint(pc_bp)
unreal.EditorAssetLibrary.save_asset(pc_bp.get_path_name())
# read back to PROVE it persisted
verify = unreal.get_default_object(pc_bp.generated_class()).get_editor_property("show_mouse_cursor")
log("BP_WallpaperPC show_mouse_cursor (readback) = {}".format(verify))

# --- GameMode that uses it -------------------------------------------------
gm_bp = _make_bp("BP_WallpaperGM", unreal.GameModeBase)
gm_cdo = unreal.get_default_object(gm_bp.generated_class())
gm_cdo.set_editor_property("player_controller_class", pc_bp.generated_class())
unreal.BlueprintEditorLibrary.compile_blueprint(gm_bp)
unreal.EditorAssetLibrary.save_asset(gm_bp.get_path_name())
gm_pc = unreal.get_default_object(gm_bp.generated_class()).get_editor_property("player_controller_class")
log("BP_WallpaperGM player_controller_class (readback) = {}".format(gm_pc))

if verify:
    log("wallpaper gamemode ready")
else:
    unreal.log_error("AgentOS make_wallpaper_gamemode: show_mouse_cursor did NOT persist")
