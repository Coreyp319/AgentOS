# UE wallpaper authoring — REFERENCE

Deep detail behind [SKILL.md](SKILL.md). Line numbers are anchors into the canonical scripts as
of 2026-06-20; treat them as approximate if the files drift. All paths relative to
`~/Documents/AgentOS` unless absolute.

---

## §1 Prerequisites & layout

- **Engine:** `~/UnrealEngine` (UE 5.8.0, RTX 4090). It is an **Installed Build**
  (`Engine/Build/InstalledBuild.txt` present) → it **cannot compile engine C++ patches** in place
  (the layer-shell path, §8, is gated on a source build). Project C++ *can* be compiled.
- **Project:** `~/UnrealProjects/AgentOSBlank/AgentOSBlank.uproject` (Blueprint-only).
  - Canonical map: `/Game/AgentOS/CalmWallpaper` (the one the harness authors + measures + ships).
  - Config: `Config/DefaultEngine.ini` (GameDefaultMap, GlobalDefaultGameMode), `Config/DefaultInput.ini`
    (mouse capture). Both backed up as `*.agentos-bak`.
- **Tooling:** `spikes/ue-probe/` (authoring + measurement) and `spikes/ue-probe/ue_wallpaper/`
  (wallpaper delivery + the nimbus-aurora launchers). Installed launchers: `~/.local/bin/nimbus-ue-wallpaper`,
  `~/.local/bin/nimbus-3d-wallpaper`.
- **Binaries:** `UnrealEditor-Cmd` (headless authoring), `UnrealEditor` (full editor; runs offscreen for
  preview), `RunUAT` (packaging).

---

## §2 Headless authoring toolchain (`author_scene.sh`)

The one safe way to run a Python authoring script against the editor headlessly.

**Launch shape:**
```
UnrealEditor-Cmd <project.uproject> [<map>] \
  -RenderOffscreen -unattended -stdout -FullStdOutLogOutput \
  -ExecCmds="py /ABSOLUTE/path/script.py, Quit"
```
`-ExecCmds` is comma-split; `Quit` exits the editor after the script returns.

### The `-ExecCmds` quoting trap (cost an 8-hour zombie; author_scene.sh:6–8, 64–71)
UE **re-wraps** a `-Key=value with spaces` token as `-Key="value with spaces"` itself. If *you* add
inner quotes around the path, the quotes collide → `FParse` reads the value as just `py ` (path lost),
the log shows `Cmd: py ` empty, `Quit` never parses either, and the editor **idles forever**.
- ❌ `-ExecCmds=py "/abs/script.py", Quit`  → broken
- ✅ `-ExecCmds=py /abs/script.py, Quit`   → UE wraps it → FParse gets the full value
Build argv as a **real bash array** (no `eval`, which collapses the escaping):
```bash
ARGS=( "$PROJECT" -RenderOffscreen -unattended -stdout -FullStdOutLogOutput
       "-ExecCmds=py ${SCENE}, Quit" )
"$CMD" "${ARGS[@]}" > "$LOG" 2>&1 &
```

### Watchdog + VRAM pre-flight (author_scene.sh:44, 52–61, 90–111)
- **VRAM gate:** read `nvidia-smi --query-gpu=memory.free`; abort (exit 3) if below `MIN_FREE_MIB`
  (default **18000** — headroom for the cold ~22 GB Lumen shader-compile spike). Override with
  `AUTHOR_FORCE=1`. Warm load+save scripts (the §8 fixes) only need `MIN_FREE_MIB=8000`.
- **Watchdog:** launch in background, poll the log for the success `MARK` (or process exit) every 3 s;
  on timeout `kill -TERM` then `kill -9`. This guarantees a quoting regression can never strand an idle
  editor again.
- **Never `setsid` here.** A setsid-detached editor gets SIGTERM'd on tool-call cleanup. (For
  *long-running* offscreen measurement, `launch_offscreen.sh` *does* use setsid deliberately.)
- **Parameterized:** `SCENE_SCRIPT=<file>` (default `scene_setup.py`) and `MARK=<grep pattern>` select
  the authoring script + its success marker. Verdict: PASS iff the `.umap` exists **and** `MARK` is in the log.

### §2.5 Packaging a cooked `-game` build (`package_game.sh`)
`RunUAT BuildCookRun` → build → cook → stage → pak → package, Development/Linux. For a Blueprint-only
project `-build` resolves to the engine's precompiled `UnrealGame` target (fast, no project C++).
- **Gotcha:** without `GameDefaultMap` (+ `MapsToCook`) under `[/Script/EngineSettings.GameMapsSettings]`
  the cook ships only the empty engine template map. Set them first.
- Output: staged under `Saved/StagedBuilds/Linux/`; launched via a staged wrapper `.sh` → stock
  `Engine/Binaries/Linux/UnrealGame` (a paked build has no literal `.uproject`).
- Cooking touches the GPU — VRAM-gate it like authoring (`UE_PROBE_ARM=1` guard in the script).
- Measured: cook ~47 s, no 22 GB spike, staged ~914 MB. Packaged boot loads CalmWallpaper in ~42 ms.

---

## §3 Scene composition recipe — the "Indigo Channel" (`indigo_channel_setup.py`)

A backlit volumetric-fog corridor: one cyan DirectionalLight backlighting fog *toward* the camera
through dark blade silhouettes → a cyan focal glow with real depth/shadow. Builders (each idempotent —
labels prefixed, assets deleted-before-create):

| Builder | Creates | Key API |
|---|---|---|
| `build_slab_material` | dark lit material for the blades | `MaterialFactoryNew` via `AssetToolsHelpers`; `MaterialEditingLibrary.create_material_expression` / `connect_material_property` / `recompile_material`; `EditorAssetLibrary.save_asset` |
| `build_blades` | 4 thin tall monolith occluders (staggered depth) | `EditorAssetLibrary.load_asset('/Engine/BasicShapes/Cube')`; `spawn_actor_from_class(StaticMeshActor)`; `smc.set_static_mesh` / `set_material`; `set_actor_scale3d`; `cast_shadow=True` |
| `build_light` | cyan **directional** shaft source | `spawn_actor_from_class(DirectionalLight)`; **`get_component_by_class(DirectionalLightComponent)`**; `mobility=MOVABLE`; `intensity`, `light_color`, `volumetric_scattering_intensity`; `cast_shadows=True`; `dynamic_shadow_distance_movable_light=8000` |
| `build_fog` | exponential height fog (the scatter medium) | `spawn_actor_from_class(ExponentialHeightFog)`; `get_component_by_class(ExponentialHeightFogComponent)`; `fog_density`, `fog_height_falloff`; **`enable_volumetric_fog=True`**; `volumetric_fog_scattering_distribution=0.85`, `_albedo`, `_distance`, `_extinction_scale` |
| `build_post` | unbound PostProcessVolume (manual exposure, DoF, bloom, grain) | `spawn_actor_from_class(PostProcessVolume)` + `unbound=True`; read `settings` struct → `override_auto_exposure_method`+`auto_exposure_method=AEM_MANUAL`, `override_auto_exposure_bias`+`auto_exposure_bias`, DoF + bloom + film-grain overrides → write `settings` back |
| `build_camera` | plain **CameraActor** (NOT CineCamera) | `spawn_actor_from_class(CameraActor)`; `get_component_by_class(CameraComponent)`; `field_of_view`; **`constrain_aspect_ratio=False`**; `auto_activate_for_player=PLAYER0` |
| `build_camera_motion` | looping LevelSequence (see §5) | gated on `INDIGO_MOTION` |
| `main` | load/build/save | `LevelEditorSubsystem.load_level`/`new_level`/`save_current_level`; clear prior actors; motion is additive (failure never blocks the save) |

**Why a plain CameraActor, not CineCamera:** a CineCamera's physical exposure crushes a dim scene to
black in `-game`. Plain camera + the manual-exposure PPV is the controllable path.

**`INDIGO_*` look knobs** (defaults = the landed "A" look):
`INDIGO_EXP_BIAS=-3.0` · `INDIGO_LIGHT_INT=2000.0` · `INDIGO_LIGHT_PITCH=-20` · `INDIGO_LIGHT_YAW=160`
· `INDIGO_VOL_SCATTER=2.0` · `INDIGO_FOG_DENSITY=0.22` · `INDIGO_DOF_FOCAL=2700` · `INDIGO_DOF_FSTOP=2.0`
· `INDIGO_BLOOM=0.40` · `INDIGO_CAM_X=-2500` · `INDIGO_FOV=75` · `INDIGO_MOTION=0` (1 to animate)
· `INDIGO_MOTION_SPEED=1.0` (0 = freeze, the reduce-motion seam).

**The three look lessons baked into the defaults:**
1. **Backlighting** — light yaws ~back (toward camera) through the blades; front-lighting = zero beams.
2. **Exposure decoupling** — bright light (fog gets real photons) + negative exposure bias (blades stay
   dark). A dim light ÷ exposure puts inscatter below the display's first code value → "no fog" when fog is on.
3. **Density reads as airborne** — denser fog makes the light read as shafts in the air, not floor-glow.

---

## §4 UE5.8 Python API gotchas (consolidated)

| Gotcha | Fix |
|---|---|
| `unreal.Color(r,g,b)` **positional args are BGRA** → warm where you meant cyan | always kwargs: `unreal.Color(r=…, g=…, b=…, a=…)` |
| `DirectionalLight` has **no `.directional_light_component`** attr | `light.get_component_by_class(unreal.DirectionalLightComponent)` |
| `volumetric_fog` is a **silent no-op** (wrong name) | `enable_volumetric_fog=True` (verify it took; a wrong name "succeeds" and does nothing) |
| Directional volumetric **shaft ≠ `cast_volumetric_shadow`** (that's point/spot) | CSM: `mobility=MOVABLE` + `cast_shadows=True` + `dynamic_shadow_distance_movable_light=<depth>` |
| `SkyLightComponent` has **no `.set_intensity()`** | `set_editor_property("intensity", …)` |
| cosmetic properties get **renamed across point-releases** → a hard fail aborts before save | wrap every cosmetic setter in `_try_set(obj, prop, val)` (logs+skips if missing); `_try_set_first(obj, [p1,p2], val)` tries candidates |
| editor-only APIs (`EditorLevelLibrary.get_editor_world()`) **null-deref/SEGFAULT in `-game`** | never call editor-subsystem APIs in a `-game` proc; use `get_game_world()` there |
| `LevelSequenceActor.playback_settings` is a **mutable struct via getter** | read it, modify fields, write it back with `set_editor_property("playback_settings", s)` |
| `loop_count` may be a **wrapper struct** (`.value=-1`) not a plain int | try the wrapper, fall back to plain int |
| PostProcessVolume `settings` is a **struct you must write back** | `s = ppv.get_editor_property("settings")` → mutate → `ppv.set_editor_property("settings", s)` |

**Subsystems:** `unreal.get_editor_subsystem(unreal.EditorActorSubsystem)` (spawn/destroy/list actors),
`unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)` (load/new/save level). **Idempotency:**
prefix script-owned actor labels and destroy-by-prefix on entry; `EditorAssetLibrary.delete_asset` before
`create_asset` (collides on existing path).

---

## §5 Motion — a saved level that animates with no Blueprint

The wallpaper runs a cooked `-game` build (no editor, no Blueprint tick). The one engine-native way to
get a saved level to animate at boot: a **LevelSequence + LevelSequenceActor with `auto_play=True`,
`loop_count=-1`**, both saved into the `.umap`.
- The sequence bakes **sine** motion as discrete keys over exactly `period_sec * fps` frames; the loop is
  seamless because the first key value == the last (`sin(2π)=0`). ~24 samples/period → smooth tangents.
- Tracks: camera lateral **Y dolly** (~120 cm / 41 s) + tiny **pitch bob** (~0.4° / 53 s) + light **yaw
  breath** (~1.5° / 67 s). The three **incommensurate** periods mean the combined path never visibly tiles.
- **Speed is baked at author time** (it scales the keyframe *time* spacing), not a runtime play-rate.
  `INDIGO_MOTION_SPEED=0` → amplitude 0 → a single flat key → identical to the static scene (the
  reduce-motion / freeze seam) with no re-author.
- MovieScene3DTransformSection exposes 9 channels: `[0,1,2]`=Loc XYZ, `[3,4,5]`=Rot Roll/Pitch/Yaw,
  `[6,7,8]`=Scale XYZ.

---

## §6 Seeing the scene — three render paths, one truth

| Path | Script | Fidelity | Use / gotcha |
|---|---|---|---|
| Editor viewport HighResShot | `preview_shot.py` | **darkest**; volumetric god-ray shafts don't show faithfully | fast iteration on layout; not the look-of-record |
| `-game` runtime | live window / `game_shot.py` | **TRUTH** (real PPV exposure, the shipped path) | `game_shot.py`'s offscreen HighResShot **SEGFAULTs** if it calls editor-only APIs in `-game`; the reliable "truth" view is a **live keep-below window + a screenshot** (`grim`/`spectacle`) |
| SceneCapture2D → RenderTarget → PNG | `capture_shot.py` | renders volumetrics reliably, but SceneCapture **self-auto-exposes** → **overexposed** unless pinned | it pins the capture's post-process exposure (AEM_MANUAL + bias) to the world PPV to match runtime — a robust offscreen self-check; caveat: if the `AgentOS_PostFX` PPV isn't found its fallback bias is wrong (over-exposes), but the normal path reads the real PPV bias |

- **RemoteExecution (RE) discovery FAILS headless** (`-RenderOffscreen`): multicast route present, no node
  discovered. Drive all headless work through launch-time `-ExecCmds`, never RE.
- `preview_shot.py` renders without RE via: `set_level_viewport_camera_info` (aim viewport at the camera),
  `editor_set_game_view(True)` (hide gizmos), a **Slate post-tick converge counter** (~120 ticks ≈ 2 s for
  Lumen GI + reflections + volumetric temporal accumulation to settle), then `HighResShot`, then flush ticks
  for the PNG to land. Re-assert the camera after the game-view toggle (it can nudge it).

---

## §7 Performance / VRAM — the throttle ladder

**The key distinction:** some levers free **VRAM capacity**, others only yield **GPU-time** (occupancy).
On a *simple* scene the ~1 GB base runtime dominates, so capacity barely moves and **GPU-time is the real
lever** — but richer scenes (textures/Nanite/more Lumen surfaces) shift that, so **re-measure**.

| Lever | Frees VRAM? | Yields GPU-time? | Notes |
|---|---|---|---|
| `sg.GlobalIlluminationQuality 0` (Lumen GI off) | **yes (largest)** | yes | biggest Lumen-VRAM release (radiance/surface cache) |
| `sg.ReflectionQuality 0` (Lumen Reflections off) | **yes** | yes | frees reflection buffers |
| `r.Streaming.PoolSize 512` (+ `LimitPoolSizeToVRAM 1`) | **yes (direct cap)** | no | hard texture-pool ceiling |
| `sg.TextureQuality 0` | yes (modest) | minor | smaller resident mips |
| `r.ScreenPercentage 50/70` | yes (render targets) | **yes (largest GPU lever)** | RT cost ∝ pixels² |
| `sg.ShadowQuality 0` / `sg.PostProcessQuality 0` | minor | **yes** | smaller shadow atlas / fewer post RTs |
| `t.MaxFPS 5/30` | **no** | **yes (occupancy only)** | yields *time*, not *capacity* — lets co-resident jobs schedule; won't free a 24 GB VRAM wall |

**Measured (RTX 4090, packaged `-game`, CalmWallpaper, 2026-06-19):**
- **FULL** (native Lumen, 1440p, uncapped): per-proc **1187–1201 MiB**, card-delta ~1.3 GB, util **96–100%**.
- **FLOOR** (Lumen GI+Refl off, pool 512, 5 fps): per-proc **970–980 MiB**, card-delta ~1.0 GB, util **~39%**.
- FULL→FLOOR on this tiny scene freed only ~250 MB (base dominates) but util 96%→39% — **GPU-time is the
  lever, VRAM isn't, yet.**
- **Cold first launch:** ~**22 GB transient** (DDC-cold shader compile), one-time; warm ≈ 1–1.3 GB. This is
  the headroom hazard the VRAM gate guards.
- nvidia-smi per-process Vulkan VRAM can undercount; the honest figure is the **card-used delta** while the
  card is otherwise calm. `measure_packaged.sh` flags `CONTAMINATED` if free VRAM drops >3 GB mid-run (a gen
  likely started). The live wallpaper's cheap throttle today = `t.MaxFPS 30` via `-ExecCmds` (util 94%→~40%).

---

## §8 Wallpaper delivery — two paths + the runtime-fit fixes

### Path A — no-build "keep below" (ships today; `ue_wallpaper/wallpaper_keepbelow.sh`)
Launch UE `-game -windowed` at the output resolution, then a KWin scripting rule sets `keepBelow=true` +
`skipTaskbar/Pager/Switcher` + `noBorder`, scoped on **both `resourceClass` ⊇ `unrealeditor` AND caption ⊇
`SF_VULKAN_SM5`** (`wallpaper_keepbelow.sh:43`) so the *editor* window (different caption) is never touched. Lands at stacking slot **[1]** — above the Plasma desktop containment
(so it covers the desktop icons, which are given up), below the panel and all app windows. Tradeoff: not
true input-passthrough.

### Path B — layer-shell engine patch (clean, GATED on a UE source build; `ue_wallpaper/run_wallpaper.sh`)
`LinuxWindow.cpp` patch (env-gated `AGENTOS_WALLPAPER=1`) sets
`SDL_PROP_WINDOW_CREATE_WAYLAND_SURFACE_ROLE_CUSTOM_BOOLEAN` at the `SDL_CreateWindowWithProperties` site →
UE's window is a bare `wl_surface`; then `dlopen(libagentos_layershell.so)` (`agentos_layershell.c`) assigns
it the `zwlr_layer_shell_v1` **BACKGROUND** role (4-edge anchor, exclusive-zone -1, keyboard-interactivity 0
→ input-less). It rides UE's existing hidden-first-present defer-gate: commit empty → ack `configure` →
UE's `Show()` flips present-enabled and Vulkan presents into the configured layer surface. **Blocked:** the
Installed engine can't compile the patch (`InstalledBuild.txt`) → needs a UE 5.8 **source build**. This is
the only path with true input passthrough.

### The four runtime-fit fixes (Path A papercuts — already applied to AgentOSBlank)
1. **Aspect** — camera `constrain_aspect_ratio=False` (else 16:9 pillarbox black bars on a 21:9 ultrawide).
   Apply via `SCENE_SCRIPT=fix_camera_aspect.py` (load level → flip flag + set aspect → save).
2. **Cursor visible** — a default `-game` PlayerController hides the OS cursor over its viewport. Create
   `BP_WallpaperPC` (`show_mouse_cursor=True`) + `BP_WallpaperGM` (`player_controller_class=BP_WallpaperPC`)
   via `SCENE_SCRIPT=make_wallpaper_gamemode.py`, then wire `DefaultEngine.ini`
   `[/Script/EngineSettings.GameMapsSettings] GlobalDefaultGameMode=/Game/AgentOS/BP_WallpaperGM.BP_WallpaperGM_C`.
   (Blueprint CDO pattern: `BlueprintFactory(parent_class)` → `create_asset` → `compile_blueprint` →
   `get_default_object(generated_class).set_editor_property(...)` → **readback to verify** → `save_asset`.)
3. **No cursor-lock on click** — `DefaultInput.ini`: `DefaultViewportMouseCaptureMode=NoCapture` +
   `DefaultViewportMouseLockMode=DoNotLock` (default `CapturePermanently_IncludingInitialMouseDown` +
   `LockOnCapture` permanently traps the cursor in UE until alt-tab).
4. **No focus-grab** — a persistent KWin rule (`~/.config/kwinrulesrc`, group `[agentos-ue-wallpaper-nofocus]`)
   scoped by **title regex `SF_VULKAN_SM5`** (`titlematch=2` = RegExp; unanchored, so it behaves like a
   substring): `acceptfocus=false` + `fsplevel=4` (Extreme) + `below=true`/`noborder=true`/`skip*`, each with
   `…rule=2` (Force). `acceptfocus=false` stops the click focus-trap; `fsplevel=4` stops the launch focus-grab.
   This persistent rule is distinct from (and complements) the live keepBelow the launcher's KWin script applies.

---

## §9 nimbus-aurora "select it as a wallpaper" integration

nimbus-aurora (`com.nimbus.aurora`, the Plasma wallpaper plugin) already launches external 3-D engines for
its "3-D engine" Styles. UE is wired in as **one more engine Style**.

- **`main.qml`** (`interactive-bg/contents/ui/`): `engineScenes = ["cyberpunk","hexen","journey","fluid",
  "lavender","ue"]`; `engine3d = cfgStyle >= 9`; `engineScene = engineLive ? engineScenes[cfgStyle-9] : ""`.
  Styles 9–13 = nimbus-flux (bevy); **Style 14 = `"ue"`** = the UE wallpaper. `syncEngine()` is called on
  `onEngineSceneChanged` / `Component.onCompleted` / `onDestruction`.
- **`config.qml`** Style dropdown: entry 14 = `i18n("Unreal · Indigo Channel · 3-D")`, under the `9: "3-D
  engine"` section. **The dropdown model and `engineScenes` must stay index-aligned.** 2-D motion presets
  only apply for index < 9 (no array-bounds risk for 14).
- **The dispatcher `nimbus-3d-wallpaper`** (and **why it exists**): Plasma's `executable` DataSource
  tokenizes via **KShell**, which expands `$HOME` but **ABORTS on shell meta-chars like `;`** — so `syncEngine`
  **cannot chain** `stop-other; start-this` in QML (that silently no-ops → nothing launches). All chaining
  lives in this bash dispatcher; QML sends a **single, metachar-free** command: `nimbus-3d-wallpaper ue
  --owner com.nimbus.aurora` / `… flux <scene> …` / `… stop`. It enforces UE↔flux **mutual exclusion**
  (start one → stop the other; slot [1] is singular).
- **`nimbus-ue-wallpaper`** (start/stop the UE engine): start = `setsid -f bash wallpaper_keepbelow.sh`
  (fully detached so it survives the launcher returning); a **watchdog** polls the appletsrc for
  `wallpaperplugin=<owner>` and stops UE when another wallpaper *plugin* is selected. (Style→style switches
  *within* aurora are handled by `syncEngine` → dispatcher, not the watchdog.)
- **THE DEPLOY GOTCHA:** plasmashell loads the **INSTALLED** plugin
  `~/.local/share/plasma/wallpapers/com.nimbus.aurora/contents/ui/`, **not the pack source**. Editing the
  pack `interactive-bg/` alone does nothing live. To deploy: copy `main.qml`+`config.qml` to the installed
  path, `rm -rf ~/.cache/qmlcache`, `systemctl --user restart plasma-plasmashell.service`. Verify the chain
  with the dispatcher's trace log (`/tmp/nimbus-3d-wallpaper.log`).
- **Adjacent gotcha:** a stray `app-nimbus-hexen-wallpaper@autostart.service` can independently launch the
  flux "hexen" shader *over* UE at slot [2]. If hexen appears unbidden, `systemctl --user stop` it (its
  `.desktop` is already `.disabled`, so it shouldn't autostart next login).

---

## §10 Key files

| Role | Path |
|---|---|
| Headless author harness (watchdog + VRAM gate) | `spikes/ue-probe/author_scene.sh` |
| Canonical scene script | `spikes/ue-probe/indigo_channel_setup.py` |
| Camera-aspect fix | `spikes/ue-probe/fix_camera_aspect.py` |
| Cursor GameMode/PC builder | `spikes/ue-probe/make_wallpaper_gamemode.py` |
| Preview / capture | `spikes/ue-probe/preview_shot.py`, `capture_shot.py` (`game_shot.py` segfaults in -game) |
| Throttle ladder + measured numbers | `spikes/ue-probe/cvar_ladder.md`, `README.md`, `measure_packaged.sh` |
| Packaging | `spikes/ue-probe/package_game.sh` |
| Keep-below wallpaper launcher | `spikes/ue-probe/ue_wallpaper/wallpaper_keepbelow.sh` |
| Layer-shell launcher + helper (gated) | `spikes/ue-probe/ue_wallpaper/run_wallpaper.sh`, `agentos_layershell.c` |
| nimbus-aurora UE engine launcher | `spikes/ue-probe/ue_wallpaper/nimbus-ue-wallpaper` → `~/.local/bin/` |
| 3-D engine dispatcher | `spikes/ue-probe/ue_wallpaper/nimbus-3d-wallpaper` → `~/.local/bin/` |
| nimbus-aurora plugin (source) | `~/whitesur-cachyos-pack/9-gpu-effects/interactive-bg/contents/ui/{main,config}.qml` |
| nimbus-aurora plugin (INSTALLED, what plasmashell loads) | `~/.local/share/plasma/wallpapers/com.nimbus.aurora/contents/ui/` |

---

## §11 Decisions & memory
- **ADR-0023** — creative-environment pipeline (the dark-ride; VRAM coexistence IS the product).
- **ADR-0029** — UE-wallpaper PRIMARY / procedural shader = fallback floor (inverts ADR-0009 for this surface).
- **ADR-0030** — reactive-wallpaper mood grammar + feed disposer (the future "scene reacts to agent state").
- **ADR-0009** — shader-primary / 3-D-as-texture (superseded in part by 0029/0030 for the wallpaper surface).
- Memory: `creative-environment-pipeline-adr-0023` (the running log + every UE gotcha as it was found).
