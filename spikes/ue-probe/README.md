# ue-probe — Phase-A feasibility harness (ADR-0023 UE-as-live-wallpaper)

Throwaway probe harness answering the **Phase-A gate** for the live-Unreal-Engine
desktop wallpaper (ADR-0023, [[creative-environment-pipeline-adr-0023]]): *can a
packaged UE 5.8 (Lumen) scene run as a yielding wallpaper that coexists on the one
RTX 4090 with the user's ComfyUI/lucid gens + Ollama, without OOM/crash?*
**VRAM management is the product** — so we measure before we architect.

The card is **shared and the user actively gens on it** (footprint cycles 2 GB↔20 GB
with no warning). Everything here is therefore VRAM-gated and PID-safe.

## The pipeline (author → preview → package → measure → coexist)

| Step | Script | What it does |
|---|---|---|
| 1. Author | `author_scene.sh` → `scene_setup.py` | Headlessly build + save `/Game/AgentOS/CalmWallpaper` (ground/cubes/spheres, golden-hour sun, SkyAtmosphere/fog, **Lumen GI+Reflections pinned**, CineCamera). VRAM pre-flight + watchdog. |
| 2. Preview | `preview_shot.py` (launch-time `-ExecCmds`) | Point the viewport at the CineCamera, game-view, `HighResShot` → clean UE-only PNG in `Saved/Screenshots/`. No Remote Execution needed. |
| 3. Package | `package_game.sh` (guard `UE_PROBE_ARM=1`) | `RunUAT BuildCookRun` → cooked Development Linux `-game` build (the *real* wallpaper runtime, not the editor). ~47 s. |
| 4. Measure | `measure_packaged.sh <label> "<rung>"` | Launch the packaged `-game` offscreen into the map at a throttle rung, confirm the map loads, sample VRAM (per-process **and** card-used-delta), report. Sustained-calm pre-flight so a gen can't pollute it. |
| — rungs | `cvar_ladder.md` | FULL / REDUCED / FLOOR `-ExecCmds` strings. The real VRAM levers = **Lumen-GI-off + Lumen-Reflections-off + streaming-pool cap**; ScreenPercentage/MaxFPS yield GPU-*time*, not capacity. |
| 5. Coexist | `coexist_runbook.md`, `coexist_inventory.md`, `comfy_load_small.py`, `ollama_load_small.sh` | Packaged-UE-FLOOR + resident Ollama + `sd_turbo`, then the heavy/video cliff (Wan ~17 GB → UE must drop to shader floor). |
| run docs | `packaged_run.md`, `remote_control_setup.md` | How to launch the packaged runtime windowed/offscreen; Remote Control (:30010) for *live* cvar throttle. |
| misc | `launch_offscreen.sh`, `sample_vram.sh`, `re_exec.py` | Editor offscreen launcher; nvidia-smi sampler; RE driver (see gotcha #3). |

## Hard-won gotchas (each of these cost real time — heed them)

1. **`-ExecCmds` quoting: NO inner quotes.** UE re-quotes the whole `-ExecCmds=`
   value itself. If you add quotes around the script path, they collide and FParse
   reads only `py ` (path lost) → `Cmd: py ` empty in the log → and since the next
   command (`Quit`) never parses either, **the editor idles forever**. Use
   `-ExecCmds=py /abs/path.py, Quit` (path has no spaces). Cost: a session + an 8 h zombie.
2. **Always run UE under a watchdog.** `author_scene.sh`/`measure_packaged.sh` kill
   the editor/game if no success marker appears within a budget — the only defense
   against a stuck headless UE silently holding VRAM for hours.
3. **Remote Execution discovery FAILS in the headless `-RenderOffscreen` editor**
   (multicast route present, `bRemoteExecution=True`, still no node). Drive headless
   work via **launch-time `-ExecCmds`** instead (`preview_shot.py`). RE is only for a
   live, visible editor.
4. **Packaged build launches via the WRAPPER `AgentOSBlank.sh`** → stock
   `Engine/Binaries/Linux/UnrealGame`. A paked build has **no literal `.uproject`**;
   the project resolves from the pak + `StagedBuild_*.ini`. Don't fabricate a
   project-named binary path.
5. **`GameDefaultMap` must point at your map** (`Config/DefaultEngine.ini`) **+
   `MapsToCook`** (`DefaultGame.ini`), or the cook ships only the engine OpenWorld
   template and you measure an empty level.
6. **Never `pkill -f` a pattern your own command line contains** (e.g. `UnrealGame`,
   `CalmWallpaper`) — it self-kills the launching shell. Track and kill PIDs.
7. **Per-process nvidia-smi VRAM can undercount Vulkan graphics memory** — trust the
   **card-used-delta from a clean baseline** as the honest footprint; treat the
   per-process number as a lower bound.
8. **Harness:** foreground `sleep`/poll-loops are blocked here (exit 144 / truncated);
   run long/polling work via background tasks. `setsid`-detached UE gets SIGTERM'd on
   tool-call cleanup — launch via background tasks, not `setsid &`.

## Findings so far (2026-06-19)

- **Authoring works** headlessly (~15 s warm DDC); preview render confirms Lumen on
  the scene. Editor authoring spikes to ~22 GB only on a cold Lumen *build*.
- **Packaging works** and is *light*: `BuildCookRun` ~47 s, no 22 GB spike (warm DDC +
  minimal blueprint content); 914 MB staged tree.
- **Packaged runtime boots offscreen, loads CalmWallpaper in 42 ms, Vulkan, no crash.**
- **FULL (native Lumen, 1440p, uncapped): UnrealGame ≈ 1.0–1.2 GB per-process** —
  vs the ~22 GB editor. Card-level delta on the first run was contaminated by a gen
  starting mid-sample (hence the sustained-calm + baseline-delta hardening).
- **PENDING:** the *clean* FULL + **FLOOR** numbers (card-delta), then the coexistence
  test, then frame-time (`stat`/CSV profiler), then the Phase-A verdict.

## Next docs owed (after the numbers settle)
Fill the asserted-number tables in `cvar_ladder.md`/`packaged_run.md`; then the real
decision: **invert ADR-0009** (UE primary, shader = fallback floor) and **extend
ADR-0023** with the measured feasibility + the Phase-B yielding-resident lease tier.
