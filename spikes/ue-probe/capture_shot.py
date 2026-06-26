# capture_shot.py — render a TRUE scene capture of the Indigo Channel from the
# AgentOS_Camera using a SceneCaptureComponent2D -> TextureRenderTarget2D -> PNG,
# headlessly, with NO Remote Execution dependency, in the EDITOR (-RenderOffscreen,
# NOT -game).
#
# WHY THIS EXISTS (both prior paths fail for VOLUMETRIC GOD-RAY verification):
#   * preview_shot.py drives the EDITOR perspective viewport (set_level_viewport_
#     camera_info + editor_set_game_view + HighResShot). That renders the editor
#     viewport's composite, which shows the height-fog inscatter BACKGROUND but does
#     NOT faithfully resolve the volumetric-fog froxel SHAFTS (the god-ray beams) —
#     the very thing we are verifying.
#   * game_shot.py launches the editor in -game -RenderOffscreen + HighResShot and
#     SIGSEGVs. The crash (ue_game_shot.log:1985-2000) is NOT in HighResShot — it is
#     `unreal.EditorLevelLibrary.get_editor_world()` null-derefing
#     FSubsystemCollectionBase::GetSubsystemInternal at tick 120: in a -game process
#     there is no editor world / editor-subsystem collection, so the deprecated
#     EditorScriptingUtilities call reads address 0x27a0 and dies before the shot.
#
#   A SceneCaptureComponent2D does a REAL off-thread scene render INTO a render
#   target — the full deferred path, volumetric fog included — exactly like the
#   game runtime, but driven from the normal editor world (no -game, no crash).
#   This is the robust self-verification capture.
#
# HOW IT'S DRIVEN (mirror of preview_shot.py — launch-time -ExecCmds, slate post-tick):
#   UnrealEditor-Cmd <proj> /Game/AgentOS/CalmWallpaper -RenderOffscreen -unattended \
#     -stdout -FullStdOutLogOutput \
#     -ExecCmds=r.VolumetricFog 1, r.VolumetricFog.GridPixelSize 4, \
#               r.VolumetricFog.GridSizeZ 256, py /abs/capture_shot.py
#   (NO -game. The fog CVARs force the volumetric path on + finer so thin shafts
#    aren't under-sampled into nothing. NO trailing Quit — this script drives its
#    own converge/capture/flush/Quit via a slate post-tick, like preview_shot.py.)
#
# OUTPUT: a 1920x1080 PNG in
#   <project>/Saved/Screenshots/Capture/Capture_<n>.png
# (a dedicated Capture/ subdir so capture_shot.sh's newest-PNG detection can't pick
#  up a stale HighResShot from LinuxEditor/.)

import os

import unreal

CAM_LABEL = "AgentOS_Camera"
PPV_LABEL = "AgentOS_PostFX"          # the manual-exposure PostProcessVolume from indigo_channel_setup.py
RES_X = 1920
RES_Y = 1080
FOV = float(os.environ.get("CAPTURE_FOV", 75.0))   # indigo default 75; PrismField cam is 52 → CAPTURE_FOV=52
CONVERGE_TICKS = 120                  # let Lumen GI + the volumetric-fog temporal history settle
CAPTURE_REPEAT = 8                    # capture_scene() a few times so the fog temporal filter converges in the RT
FLUSH_TICKS = 90                      # keep ticking after export so the PNG flushes to disk

OUT_SUBDIR = "Capture"                # Saved/Screenshots/Capture/
OUT_BASENAME = "Capture"

# A SAVED render-target asset path. We create -> use -> DELETE it (a transient RT can
# be flaky to export headlessly; a saved asset under /Game is robust, and we clean it
# up so we leave no diff). build/teardown both guarded.
RT_ASSET_DIR = "/Game/AgentOS"
RT_ASSET_NAME = "RT_Indigo"
RT_ASSET_PATH = RT_ASSET_DIR + "/" + RT_ASSET_NAME

_eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
_ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)


def _log(m):
    unreal.log("[capture] " + str(m))


def _err(m):
    unreal.log_error("[capture] " + str(m))


# ---------------------------------------------------------------------------
# Editor world (NOT a -game world). This is the call game_shot.py got WRONG:
# it used the DEPRECATED unreal.EditorLevelLibrary.get_editor_world(), which
# null-derefs the editor-subsystem collection under -game. We run in the real
# editor and use the non-deprecated UnrealEditorSubsystem.get_editor_world().
# ---------------------------------------------------------------------------
def _editor_world():
    try:
        w = _ues.get_editor_world()
        if w is not None:
            return w
    except Exception as exc:  # noqa: BLE001
        _err("UnrealEditorSubsystem.get_editor_world failed: {}".format(exc))
    return None


def _find_by_label(label):
    for a in _eas.get_all_level_actors():
        try:
            if a.get_actor_label() == label:
                return a
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Read the world's manual-exposure bias off the AgentOS_PostFX PostProcessVolume.
# CRITICAL: a SceneCaptureComponent2D does NOT inherit world PostProcessVolume
# settings and runs its OWN auto-exposure by default — so without this the capture
# would be exposure-mismatched (typically too dark / "breathing") vs the real -game
# runtime. We copy the world's AEM_MANUAL + bias onto the capture's own
# post_process_settings (post_process_blend_weight = 1.0) so brightness matches.
# Returns (method_enum, bias_float). Falls back to manual + the setup default bias
# (1.0) if the PPV or props can't be read, so the capture is never accidentally
# auto-exposed.
# ---------------------------------------------------------------------------
_DEFAULT_BIAS = 1.0   # indigo_channel_setup.py INDIGO_EXP_BIAS default


def _read_world_exposure():
    ppv = _find_by_label(PPV_LABEL)
    if ppv is None:
        _log("WARN: {} PPV not found; using AEM_MANUAL bias={} fallback".format(
            PPV_LABEL, _DEFAULT_BIAS))
        return unreal.AutoExposureMethod.AEM_MANUAL, _DEFAULT_BIAS
    try:
        s = ppv.get_editor_property("settings")
        bias = float(s.get_editor_property("auto_exposure_bias"))
        method = s.get_editor_property("auto_exposure_method")
        _log("world PPV exposure: method={} bias={}".format(method, bias))
        return method, bias
    except Exception as exc:  # noqa: BLE001
        _log("WARN: could not read PPV exposure ({}); AEM_MANUAL bias={} fallback".format(
            exc, _DEFAULT_BIAS))
        return unreal.AutoExposureMethod.AEM_MANUAL, _DEFAULT_BIAS


def _make_render_target():
    """Create the RT as a SAVED asset (robust headless export), 1920x1080, RGBA8 so
    SCS_FINAL_COLOR_LDR maps 1:1 to the PNG. Returns the RT or None."""
    # idempotent: delete a stale one first.
    try:
        if unreal.EditorAssetLibrary.does_asset_exist(RT_ASSET_PATH):
            unreal.EditorAssetLibrary.delete_asset(RT_ASSET_PATH)
            _log("deleted prior RT asset {}".format(RT_ASSET_PATH))
    except Exception as exc:  # noqa: BLE001
        _log("RT pre-delete skip: {}".format(exc))

    assets = unreal.AssetToolsHelpers.get_asset_tools()
    try:
        factory = unreal.TextureRenderTarget2DFactoryNew()
        rt = assets.create_asset(RT_ASSET_NAME, RT_ASSET_DIR,
                                 unreal.TextureRenderTarget2D, factory)
    except Exception as exc:  # noqa: BLE001
        _err("create_asset(TextureRenderTarget2D) failed: {}".format(exc))
        rt = None

    if rt is None:
        # Fallback: transient RT via RenderingLibrary (RGBA8 for LDR final color).
        world = _editor_world()
        try:
            rt = unreal.RenderingLibrary.create_render_target2d(
                world, RES_X, RES_Y,
                unreal.TextureRenderTargetFormat.RTF_RGBA8,
                unreal.LinearColor(0.0, 0.0, 0.0, 1.0))
            _log("created TRANSIENT render target via RenderingLibrary")
        except Exception as exc:  # noqa: BLE001
            _err("create_render_target2d fallback failed: {}".format(exc))
            return None

    # Size + format (the factory default is 256x256 / RGBA16F — fix both).
    try:
        rt.set_editor_property("render_target_format",
                               unreal.TextureRenderTargetFormat.RTF_RGBA8)
    except Exception as exc:  # noqa: BLE001
        _log("RT format set skip: {}".format(exc))
    try:
        rt.resize_target(RES_X, RES_Y)
    except Exception as exc:  # noqa: BLE001
        # older API: size_x / size_y props.
        _try = False
        try:
            rt.set_editor_property("size_x", RES_X)
            rt.set_editor_property("size_y", RES_Y)
            _try = True
        except Exception:
            pass
        if not _try:
            _log("RT resize skip: {}".format(exc))
    _log("render target ready {}x{} RGBA8".format(RES_X, RES_Y))
    return rt


def _spawn_capture(cam, rt, method, bias):
    """Spawn a SceneCapture2D at the camera transform, point its component at the RT,
    final-color-LDR source, FOV matched, and stamp the world's manual exposure onto
    the capture's OWN post-process so brightness equals the -game runtime."""
    loc = cam.get_actor_location()
    rot = cam.get_actor_rotation()
    cap_actor = _eas.spawn_actor_from_class(unreal.SceneCapture2D, loc, rot)
    cap_actor.set_actor_label("AgentOS_Capture")

    comp = cap_actor.get_component_by_class(unreal.SceneCaptureComponent2D)
    if comp is None:
        _err("SceneCapture2D has no SceneCaptureComponent2D")
        return None, None

    comp.set_editor_property("texture_target", rt)
    # Final post-processed LDR color = volumetric fog + exposure + DoF + bloom, the
    # full composite — exactly what the runtime shows.
    comp.set_editor_property("capture_source",
                             unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
    comp.set_editor_property("fov_angle", FOV)

    # We drive capture_scene() explicitly; do NOT capture every frame (LDR final
    # color + capture_every_frame is a known black-RT footgun) and do NOT capture on
    # movement.
    for p in ("capture_every_frame", "capture_on_movement"):
        try:
            comp.set_editor_property(p, False)
        except Exception as exc:  # noqa: BLE001
            _log("capture flag {} skip: {}".format(p, exc))

    # ---- EXPOSURE MATCH (the load-bearing bit) ----------------------------------
    # The capture ignores the world PPV and self-auto-exposes by default; force its
    # own post-process to the SAME manual method + bias, full blend weight, so the
    # render-target brightness equals the manual-exposed -game runtime.
    try:
        comp.set_editor_property("post_process_blend_weight", 1.0)
    except Exception as exc:  # noqa: BLE001
        _log("post_process_blend_weight skip: {}".format(exc))
    try:
        s = comp.get_editor_property("post_process_settings")
        s.set_editor_property("override_auto_exposure_method", True)
        s.set_editor_property("auto_exposure_method", method)
        s.set_editor_property("override_auto_exposure_bias", True)
        s.set_editor_property("auto_exposure_bias", bias)
        # Pin min==max so even if a non-manual method leaks through, metering is fixed
        # (belt-and-braces against the capture "breathing").
        for ov, pr, val in (
            ("override_auto_exposure_min_brightness", "auto_exposure_min_brightness", 1.0),
            ("override_auto_exposure_max_brightness", "auto_exposure_max_brightness", 1.0),
        ):
            try:
                s.set_editor_property(ov, True)
                s.set_editor_property(pr, val)
            except Exception:
                pass
        comp.set_editor_property("post_process_settings", s)
        _log("capture exposure pinned: method={} bias={} blend=1.0".format(method, bias))
    except Exception as exc:  # noqa: BLE001
        _err("could not pin capture exposure ({}); PNG brightness may MISMATCH -game".format(exc))

    _log("SceneCapture2D spawned at cam loc={} rot={} fov={}".format(loc, rot, FOV))
    return cap_actor, comp


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
_cam = _find_by_label(CAM_LABEL)
if _cam is None:
    _err("{} not found — cannot capture. (Is /Game/AgentOS/CalmWallpaper the loaded map?)".format(
        CAM_LABEL))

_method, _bias = _read_world_exposure()
_rt = _make_render_target() if _cam is not None else None
_cap_actor, _comp = (None, None)
if _cam is not None and _rt is not None:
    _cap_actor, _comp = _spawn_capture(_cam, _rt, _method, _bias)

_ready = _comp is not None and _rt is not None
_out_dir = os.path.join(
    unreal.Paths.project_saved_dir(), "Screenshots", OUT_SUBDIR)
try:
    os.makedirs(_out_dir, exist_ok=True)
except Exception as exc:  # noqa: BLE001
    _log("mkdir {} skip: {}".format(_out_dir, exc))

_state = {"n": 0, "captured": False, "exported": False}


def _cleanup():
    # remove the spawned capture actor + the saved RT asset so we leave NO diff.
    try:
        if _cap_actor is not None:
            _eas.destroy_actor(_cap_actor)
    except Exception as exc:  # noqa: BLE001
        _log("capture-actor cleanup skip: {}".format(exc))
    try:
        if unreal.EditorAssetLibrary.does_asset_exist(RT_ASSET_PATH):
            unreal.EditorAssetLibrary.delete_asset(RT_ASSET_PATH)
            _log("deleted RT asset {}".format(RT_ASSET_PATH))
    except Exception as exc:  # noqa: BLE001
        _log("RT asset cleanup skip: {}".format(exc))


def _on_tick(_delta):
    _state["n"] += 1
    n = _state["n"]

    if not _ready:
        # nothing to do; emit the DONE marker once so the harness exits promptly.
        if n >= 5:
            _err("NOT READY (cam/RT/capture missing) — emitting DONE so harness exits")
            _log("DONE capture_shot (no capture)")
            try:
                unreal.unregister_slate_post_tick_callback(_handle)
            except Exception:
                pass
            unreal.SystemLibrary.execute_console_command(_editor_world(), "Quit")
        return

    # 1) converge: let Lumen GI + the volumetric-fog temporal history settle.
    if not _state["captured"] and n >= CONVERGE_TICKS:
        # Capture the scene several times so the fog/Lumen temporal accumulation
        # converges INSIDE the render target (one capture_scene can show ghosting/
        # under-converged shafts).
        try:
            for _ in range(CAPTURE_REPEAT):
                _comp.capture_scene()
            _state["captured"] = True
            _log("capture_scene() x{} issued (tick {})".format(CAPTURE_REPEAT, n))
        except Exception as exc:  # noqa: BLE001
            _err("capture_scene failed: {}".format(exc))
            _state["captured"] = True  # don't spin forever

    # 2) export the render target to PNG (one tick after capture so the GPU readback
    #    is in).
    elif _state["captured"] and not _state["exported"] and n >= CONVERGE_TICKS + 2:
        world = _editor_world()
        ok = False
        try:
            unreal.RenderingLibrary.export_render_target(
                world, _rt, _out_dir, OUT_BASENAME + ".png")
            ok = True
        except Exception as exc:  # noqa: BLE001
            _err("export_render_target failed: {}".format(exc))
        _state["exported"] = True
        if ok:
            _log("export_render_target -> {}/{}.png".format(_out_dir, OUT_BASENAME))

    # 3) flush, clean up, mark DONE, and Quit (so the watchdog isn't needed).
    elif _state["exported"] and n >= CONVERGE_TICKS + 2 + FLUSH_TICKS:
        _cleanup()
        _log("DONE capture_shot (tick {})".format(n))
        try:
            unreal.unregister_slate_post_tick_callback(_handle)
        except Exception:
            pass
        # Quit reliably so capture_shot.sh's watchdog is a backstop, not the trigger.
        unreal.SystemLibrary.execute_console_command(_editor_world(), "Quit")


_handle = unreal.register_slate_post_tick_callback(_on_tick)
_log("tick callback registered (converge={} ticks, capture x{}, exposure method={} bias={})".format(
    CONVERGE_TICKS, CAPTURE_REPEAT, _method, _bias))
