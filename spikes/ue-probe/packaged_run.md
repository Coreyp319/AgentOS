# Running the packaged AgentOSBlank `-game` build (the wallpaper runtime)

After `package_game.sh` (DO NOT run during a live VRAM measurement), the runnable
binary lives at:

```
# VERIFIED 2026-06-19 — the path below is the LAUNCH WRAPPER, not a project binary.
# A paked build has NO literal .uproject and NO project-named exe; the blueprint-only
# project ships the stock 'UnrealGame' target and resolves the project from the
# mounted pak + Engine/Config/StagedBuild_AgentOSBlank.ini via the path the wrapper
# passes. Launch through the wrapper:
~/UnrealProjects/AgentOSBlank/Saved/StagedBuilds/Linux/AgentOSBlank.sh
#   -> runs  Engine/Binaries/Linux/UnrealGame  "<logical .uproject>"  "$@"
# (or the same tree under Saved/ArchivedBuilds/Linux/ because we passed -archive)
#
# GOTCHA: the packaged build only boots into /Game/AgentOS/CalmWallpaper because we
# set GameDefaultMap (+ MapsToCook) in Config/DefaultEngine.ini / DefaultGame.ini.
# Without that the cook ships only the engine OpenWorld template and you measure an
# empty map. The turnkey measurement harness is `measure_packaged.sh`.
```

This is the **packaged game**, not `UnrealEditor-Cmd`. That distinction is the
whole point of Phase-A: the editor carries ~3 GB+ of editor subsystems as a
co-tenant; the cooked `-game` runtime loads only the cooked level + the runtime
modules, so it is a far lighter co-resident on the 4090 next to ComfyUI +
Ollama.

---

## 1. The launch line agentosd will use (windowed, throttled, RC up)

```bash
WRAP=~/UnrealProjects/AgentOSBlank/Saved/StagedBuilds/Linux/AgentOSBlank.sh

bash "$WRAP" \
  -windowed -ResX=2560 -ResY=1440 -WinX=0 -WinY=0 \
  `# windowed at the desktop resolution, pinned top-left; KWin reparents it to` \
  `# the wallpaper layer (the compositor wiring is the separate KWin spike).` \
  -ExecCmds="WebControl.StartServer, t.MaxFPS 30, r.ScreenPercentage 50" \
  `# start the Remote Control HTTP server AND set a conservative initial rung of` \
  `# the throttle ladder before the first frame, so we never spike VRAM/clock on` \
  `# launch next to ComfyUI. agentosd then drives further rungs over HTTP.` \
  -RCWebControlEnable \
  `# belt-and-braces enable for RC in a packaged build (see remote_control_setup.md CAVEAT)` \
  -nosplash -nosound \
  `# no splash window, no audio device grab (it is wallpaper, not a game)` \
  -log
  `# stdout/file log so we can confirm ':30010' opened and read frame timings`
```

Flags are recognized UE command-line args [DOC]; `-ResX`/`-ResY`/`-windowed`/
`-fullscreen`/`-log`/`-game` are documented, the Linux invocation form is
`./TheProjectBinary -windowed -ResX=1000 -ResY=600` [DOC].

### Borderless
There is no single official `-borderless` switch that is reliable across
versions; the dependable route to a chromeless wallpaper window is **windowed +
let the Wayland compositor (KWin) strip decorations and place it on the
background layer**. Drive window mode/size from config too if needed:
`GameUserSettings.ini → FullscreenMode=2` (2 == Windowed Borderless) is the
in-engine equivalent; prefer compositor control on Wayland.

---

## 2. Headless / measurement mode: `-RenderOffscreen` vs `-nullrhi`

For VRAM measurement of the runtime **without** a visible window:

| Flag | GPU | Renders | Window | Use for |
|---|---|---|---|---|
| `-RenderOffscreen` | **yes, real GPU** | yes (to offscreen target) | none | **measuring the true VRAM/render cost** of the wallpaper headlessly |
| `-nullrhi` | no | no | none | a pure logic/no-render smoke test; tells you ~nothing about render VRAM |

So for the co-tenancy VRAM study, the runtime to measure is:

```bash
bash "$WRAP" -RenderOffscreen -ResX=2560 -ResY=1440 \   # measure_packaged.sh automates this + 1Hz VRAM sampling
  -ExecCmds="WebControl.StartServer, t.MaxFPS 30, r.ScreenPercentage 50" \
  -RCWebControlEnable -nosound -nosplash -log
```

`-RenderOffscreen` still does real GPU rendering with no visible window
(designed for the no-physical-display / pixel-streaming case) — that is what you
want for an honest VRAM number [DOC]. `-nullrhi` skips window creation **and all
rendering**, so it would under-report VRAM to near zero and is only useful as a
"does the cook boot at all" check [DOC].

[CAVEAT] `-RenderOffscreen` has a known sharp edge: it does not always let you
pick the GPU, and some plugins (e.g. NVIDIA Streamline/DLSS) have crashed under
it in packaged 5.x builds. AgentOSBlank is a stock blank project with no DLSS, so
this should be clear — but watch the log on first run.

---

## 3. Expected VRAM advantage vs the editor (why this whole exercise)

- The editor (`UnrealEditor`) loads the full editor toolset and is the heavy
  co-tenant the task flags at ~3 GB+ baseline before any scene.
- A cooked, packaged `-game` build loads only the cooked level's assets + the
  runtime modules — community evidence is that a minimal packaged level's
  footprint is a small fraction of the editor's; offscreen/container rendering of
  packaged UE projects is a standard, comparatively-light workload (the Unreal
  Containers project documents running packaged builds headless with
  `-RenderOffscreen` precisely because the packaged footprint is tractable)
  [DOC, forum-evidence]. The exact delta on this box is what the (separately-run)
  measurement establishes — but directionally the packaged `-game` is the
  light co-tenant and the editor is not.
- With Lumen on a blank level the dominant cost is the render target +
  Lumen scene at the chosen `r.ScreenPercentage`; that is exactly what agentosd's
  throttle ladder (driven over Remote Control, see `remote_control_setup.md`)
  exists to clamp when ComfyUI or Ollama needs the VRAM back.

> Numbers are deliberately NOT asserted here — capturing them is the heavy job
> we are forbidden from running now. This doc says *which binary to measure*
> (`-RenderOffscreen` packaged `-game`) and *which knobs* clamp it.

---

## Sources
- Command-line arguments (`-game`, `-fullscreen`, `-windowed`, `-ResX`/`-ResY`, `-log`; Linux `./Binary -windowed -ResX= -ResY=` form): <https://dev.epicgames.com/documentation/unreal-engine/command-line-arguments-in-unreal-engine>
- `-RenderOffscreen` renders on the real GPU with no window (vs `-nullrhi` = no render), packaged-build offscreen rendering: <https://unrealcontainers.com/blog/offscreen-rendering-in-windows-containers/>, <https://unrealcontainers.com/docs/use-cases/linear-media>
- `-RenderOffscreen` GPU-selection / plugin sharp edges (CAVEAT): <https://forums.unrealengine.com/t/unable-to-select-gpu-when-using-renderoffscreen/478045>, <https://forums.developer.nvidia.com/t/streamline-plugin-crashes-in-unreal-5-3-packaged-game-with-renderoffscreen-command/268872>
