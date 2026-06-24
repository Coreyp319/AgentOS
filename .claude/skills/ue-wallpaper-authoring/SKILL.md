---
name: ue-wallpaper-authoring
description: Playbook for agentically authoring UE 5.8 real-time Lumen "dark-ride" scenes (the "Indigo Channel" backlit-fog tableau) HEADLESSLY on Linux and shipping them as the live desktop wallpaper on KWin/Plasma 6. Covers the headless UnrealEditor-Cmd + `-ExecCmds` Python toolchain (and the quoting trap that idles the editor forever), the scene-composition recipe (backlit volumetric fog, occluders, manual exposure, LevelSequence motion) with every UE5.8 Python API gotcha (BGRA colors, enable_volumetric_fog, CSM shafts, editor-APIs-segfault-in-game), previewing/capturing the look, the VRAM/GPU-time throttle ladder + measured footprint, and the wallpaper delivery (no-build keep-below vs the gated layer-shell patch) wired as a selectable nimbus-aurora Style. Use when authoring/editing a UE wallpaper scene, debugging the headless author/preview/package flow, fixing the wallpaper's fit (aspect/cursor/focus), wiring the nimbus-aurora option, or planning the creative-environment (ADR-0023/0029/0030) roadmap.
---

# UE real-time wallpaper authoring (AgentOS "creative environment")

The bet (ADR-0029, inverts ADR-0009 for this surface): the desktop wallpaper is a **live
UE 5.8 Lumen environment** — a Disneyland dark-ride tableau on a camera track — with the
procedural shader as the **fallback floor**, not the star. VRAM coexistence on the shared
4090 *is* the product (ADR-0023). Decisions: `docs/adr/0023`, `0029`, `0030`. Memory:
`creative-environment-pipeline-adr-0023`. Tooling: **`spikes/ue-probe/`** (+ `ue_wallpaper/`).
Project: `~/UnrealProjects/AgentOSBlank` → map `/Game/AgentOS/CalmWallpaper`. Deep detail:
[REFERENCE.md](REFERENCE.md).

## The pipeline (author → fit → select)
```sh
cd ~/Documents/AgentOS

# 1. AUTHOR the scene headlessly into /Game/AgentOS/CalmWallpaper (VRAM-gated + watchdog).
#    Tune the look with INDIGO_* env knobs; INDIGO_MOTION=1 adds the looping parallax.
SCENE_SCRIPT=indigo_channel_setup.py MARK='Indigo Channel scene built' \
  INDIGO_EXP_BIAS=-3 INDIGO_LIGHT_INT=2000 INDIGO_FOG_DENSITY=0.22 INDIGO_MOTION=1 \
  bash spikes/ue-probe/author_scene.sh        # → PASS + saved .umap

# 2. SEE it (truth = a live -game window; editor preview is darker, see REFERENCE §6).
python3 -                                     # or: the keep-below launcher below, then screenshot

# 3. SELECT it as the wallpaper. It's a real nimbus-aurora Style now:
#    right-click desktop → Configure Wallpaper → Style → "Unreal · Indigo Channel · 3-D".
#    Or drive the no-build keep-below path directly:
~/.local/bin/nimbus-ue-wallpaper              # start: UE -game at wallpaper slot [1]
~/.local/bin/nimbus-ue-wallpaper --stop       # stop
```
The per-project "wallpaper fit" fixes (camera fills ultrawide, cursor visible, no focus-trap)
are already applied to AgentOSBlank; re-run / re-derive them via REFERENCE §8 for a new project.

## The rules that keep mattering
1. **`-ExecCmds` quoting will idle the editor forever.** Pass `-ExecCmds=py /abs/path.py, Quit`
   with **NO inner quotes** — UE re-wraps the token itself; inner quotes collide → FParse reads
   `py ` (path lost), `Quit` never fires, editor idles. Build argv as a bash **array**, no `eval`.
   **Always** run headless UE under a **watchdog** (`author_scene.sh`). (REF §2)
2. **Three render paths disagree on exposure; `-game` is the truth.** Editor preview is darkest
   and won't show god-ray shafts faithfully; SceneCapture2D self-auto-exposes (blown unless
   pinned). And **editor-only APIs (`EditorLevelLibrary.get_editor_world()`) SEGFAULT in `-game`** —
   never call them there. (REF §6)
3. **God-rays need BACKLIGHTING + decoupled exposure.** Light must shine *toward* the camera
   through occluders; use a **bright** light (~2000 lux) + a **negative** exposure bias (~-3) so the
   shaft is the brightest thing in a dark room. A dim light ÷ exposure = invisible shaft. (REF §3)
4. **`enable_volumetric_fog` (not `volumetric_fog` — silent no-op).** The directional shaft is
   **CSM** (`cast_shadows` + `dynamic_shadow_distance_movable_light` on a MOVABLE light), not the
   point/spot `cast_volumetric_shadow`. (REF §3/§4)
5. **`unreal.Color(...)` positional args are BGRA — always use kwargs.** Wrap every cosmetic
   setter in `_try_set` (UE renames properties across point-releases; a hard fail aborts the save). (REF §4)
6. **VRAM: cold first author spikes ~22 GB (DDC shader compile); warm ~1–1.3 GB.** Always
   VRAM-gate (shared 4090). On a simple scene the throttle lever is **GPU-time** (`t.MaxFPS`), NOT
   VRAM — the ~1 GB base dominates; Lumen-off + streaming-pool-cap are the capacity levers. (REF §7)
7. **Plasma loads the INSTALLED plugin, not the pack source.** Deploy edits to
   `~/.local/share/plasma/wallpapers/com.nimbus.aurora/contents/ui/`, clear `~/.cache/qmlcache`,
   `systemctl --user restart plasma-plasmashell.service`. And QML **can't chain commands** (Plasma's
   executable DataSource tokenizes via KShell, which aborts on `;`) → all chaining lives in the
   `nimbus-3d-wallpaper` bash dispatcher. (REF §9)
8. **The clean input-less wallpaper is gated on a UE SOURCE build.** The installed engine can't
   compile the layer-shell `LinuxWindow.cpp` patch. The no-build **keep-below** path ships today
   (covers desktop icons; not true input-passthrough — mitigated by `acceptfocus=false` + NoCapture). (REF §8)

## Common tasks
- **Author / re-tune a scene** → REF §3 (recipe + INDIGO_* knobs), §4 (API gotchas), §5 (motion).
- **Debug the headless author/quoting/watchdog flow** → REF §2.
- **See the real look / why the preview lies** → REF §6.
- **Make it fit as a wallpaper (aspect, cursor, focus)** → REF §8 (the four runtime-fit fixes).
- **Wire / debug the nimbus-aurora "select it" option** → REF §9 (dispatcher, deploy gotcha).
- **Pick a throttle rung / budget VRAM for coexistence** → REF §7.
- **Package a cooked `-game` build** → REF §2.5.

## Hard constraints (non-negotiable)
- **VRAM-gate everything.** Shared RTX 4090; Corey gens constantly (2↔21 GB). Cold authoring can
  need ~22 GB — `author_scene.sh` refuses below `MIN_FREE_MIB`. Never OOM a running gen. **Never read
  `~/ComfyUI/output` or `/queue`.**
- **Reversible + model-proposes/code-disposes.** A scene is a saved `.umap` artifact; the wallpaper
  is a *selectable* option (no silent desktop mutation); every config edit is backed up (`*.agentos-bak`).
- **ADR discipline.** UE-primary/shader-floor is ADR-0029; the dark-ride + dual-purpose track is
  ADR-0023; reactive mood is ADR-0030. Changing behaviour → amend an ADR.
- **`spikes/` is throwaway.** The authoring scripts live there today; graduate the durable tooling out
  of `spikes/ue-probe/` when the feature productizes (don't let a `spikes/` cleanup delete the pipeline).
