# Prism Field — UE 5.8 dispersive-glass wallpaper

The 3-D sibling of the 2-D Aurora "Prism" shader (Style 15). A **high-key** studio
tableau (the inverse of the dark Indigo Channel): a dark central monolith orbited by
glass **coronas** that show **real view-dependent chromatic dispersion** — true prism
fringing on actual geometry, not a flat post-process — plus a ribbon vortex, on a
near-white field. Built toward `~/Downloads/personalized_listening__geshsqt82yeu_large.jpg`.

- Scene script: `prism_field_setup.py` → map `/Game/AgentOS/PrismField`
- Materials: `M_AgentOS_Prism` (the corona), `M_AgentOS_Backdrop` (unlit white field),
  `M_AgentOS_PrismSlab` (dark form), `M_AgentOS_Ribbon`; reuses `MPC_AgentOS_Reactive`.
- Decisions: dispersion = Recipe A (Fresnel/refraction now; Substrate thin-film deferred);
  centre = abstract dark form. See `creative-environment-pipeline-adr-0023` memory + the
  `ue-wallpaper-authoring` skill.

## FIXED 2026-06-24 — "renders in capture, BLACK in `-game`"
Root cause (verified against `Engine/Classes/Engine/Scene.h`): the PostProcessVolume used
`AEM_MANUAL` exposure with **Apply Physical Camera Exposure left ON (the default)**. That
property's own tooltip is *"Only affects Manual exposure mode"* — in manual mode it makes the
exposure use the **player camera's physical settings** (f/2.8, 1/60s, ISO100 ≈ EV100 ~9),
which crushes a high-key scene (emissive ~1.0) to **black in `-game`**. A `SceneCaptureComponent2D`
has **no** physical camera, so the offscreen preview rendered correctly WHITE — that asymmetry
(capture white, `-game` black, *same map*) was the entire bug. It was never the actors, the
camera/view-target, GI, distance fields, or unlit-vs-lit (all ruled out by a `-game` probe that
found 46 actors + the AgentOS_Camera correctly set as the view target, then a windowed capture
that showed the scene rendering near-black).
**Fix** (`prism_field_setup.py` `build_post`): `auto_exposure_apply_physical_camera_exposure=False`
+ pinned `min/max_brightness=1.0`, so `-game` exposure == the proven capture. Verified: the
windowed `-game` mean brightness went 0.0 → 0.96 and the full composition renders (see the
reference look). Diagnostic tool: `pf_game_diag.py` (the `-game`-safe world/actor/camera probe —
editor subsystems are None in `-game`, so it grabs the world via `find_object`).

**Second, separate gotcha** (also produced a black window): a NON-`-unattended` `UnrealEditor -game`
launch pops a **modal "Target Upgrade Required" zenity dialog** (the project Target.cs are at
`BuildSettingsVersion.V5`) and BLOCKS before the map loads. `wallpaper_keepbelow.sh` now passes
`-unattended`. Proper root fix = bump both `Source/*.Target.cs` to `V7` (forces a C++ rebuild).

## How the dispersion works (the load-bearing idea)
A single `MaterialExpressionCustom` HLSL node (minimal wire surface) ports `aurora.frag`'s
`prismCorona` math. It samples the rim crest **per-channel at r∓ca** (the chromatic split);
where the three crests **overlap** the ring is **white**, where they **diverge** at the
inner/outer edges they fan into prism colours (red outer, blue inner) — physical chromatic
aberration. A **dark glass body** drives opacity so the ring **occludes the white field**
(a purely-additive emissive ring is invisible on white — this was the key fix). The corona
"turns slowly" via a **Time-driven phase in the material** (like the shader's `spin=0.05*t`),
so no fragile per-ring transform animation is needed.

## Author it (VRAM-gated; needs a clear card — ~18 GB free)
```sh
cd ~/Documents/AgentOS
SCENE_SCRIPT=prism_field_setup.py MARK='Prism Field scene built' \
  bash spikes/ue-probe/author_scene.sh
```
Note: `author_scene.sh`'s final PASS line checks the stale `CalmWallpaper.umap` path; the
real signal is the `Prism Field scene built` MARK (only emitted when `_FAIL` is empty) +
a fresh `Content/AgentOS/PrismField.umap`. Verify wire health:
`grep -aE 'WIRE FAILED|BUILD HAD' spikes/ue-probe/ue_author.log`.

## Preview it (offscreen, bloom-less — a rough proxy)
```sh
MAP=/Game/AgentOS/PrismField bash spikes/ue-probe/capture_shot.sh
# → ~/UnrealProjects/AgentOSBlank/Saved/Screenshots/Capture/Capture.png
```
The SceneCapture has **no bloom** and uses FOV 75 (the real camera is 52°). The bright
white rim cores BLOOM in a real `-game` run, softening the fringes toward the reference —
so the **live `-game` window is the look-of-record**, not this capture.

## Look dials (env knobs)
| Knob | Default | Effect |
|---|---|---|
| `PRISM_REFRACT` | 1 | translucent glass + real refraction; 0 = cheap masked-emissive |
| `PRISM_BACKDROP` | 1.8 | white-field emissive (≈ white through ACES; raise to brighten) |
| `PRISM_DISP` | 0.05 | dispersion width — bigger = wider/more colourful fringe, smaller = whiter core |
| `PRISM_HUE_SPREAD` | 0.5 | 0 = theme-tinted (cyan/violet) fringe, 1 = full ROYGBIV vivid |
| `PRISM_RIM_BRIGHT` | 7.0 | rim emissive gain (bloom past `PRISM_BLOOM_THRESH`) |
| `PRISM_BLOOM_THRESH` | 2.5 | above the white field so only the bright rims bloom |
| `PRISM_SPIN` | 0.06 | corona turn rate (0 = still) |
| `PRISM_EXP_BIAS` | 0.0 | AEM_MANUAL exposure (high-key sits near 0) |
| `PRISM_RIBBON` | 1 | build the lower-third ribbon vortex |

## Ship it as a wallpaper (owed, on-box)
1. See it live: `~/.local/bin/nimbus-ue-wallpaper` runs the keep-below `-game` window —
   but it targets the cooked build / `GameDefaultMap`. To ship Prism Field as its own
   selectable Style, set `GameDefaultMap=/Game/AgentOS/PrismField` (+ MapsToCook) and cook
   (`package_game.sh`), or add a second nimbus-aurora engine Style alongside Style 14
   (see the `ue-wallpaper-authoring` skill §9 — `engineScenes`/dropdown index-aligned).
2. Reactivity: `agentosd scene` + `agentosd rc` already push the MPC axes this material taps
   (Motion/Warm/Desat/ReduceMotion); idle deltas are 0 ⇒ byte-identical to the static look.

## Deferred upgrade — Substrate thin-film (true interference iridescence)
`r.Substrate=1` in `Config/DefaultEngine.ini` (back up first) recompiles ALL project shaders
(risks the Indigo scene; cold ~22 GB spike — VRAM-gate). `MaterialExpressionSubstrateSlabBSDF`
IS Python-reachable; thin-film property names are VERIFY-LIVE. Treat as an ADR-gated
experiment on a project copy, measured for Δfps before adopting.
