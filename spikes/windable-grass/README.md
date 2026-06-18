# spike: windable-grass

Throwaway. The **live / interactive mode** of the creative-environment pipeline
([design-0023](../../docs/design/0023-creative-environment-pipeline.md), the *canonical
first sample* — "a waving amber field of grass, stylized like an oil painting").

A **painterly amber grass field** rendered as a procedural GLSL shader whose **wind
direction and gust strength are live uniforms** — so the wind can *redirect in real time*.
The rendered Blender clip (the EEVEE artifact) can't react — it's pre-baked, one wind
direction baked into its pixels. This real-time shader is where the interactivity lives.

## What it is (the ADR-0009 reconciliation)

This does **not** breach [ADR-0009](../../docs/adr/0009-dreaming-shader-primary-video-as-texture.md)
(procedural shader = primary live renderer; video/3D = texture-input + on-demand). It
routes the live scene the way ADR-0009 already routes generated video:

- the **baked render carries the *look*** — palette + painterly structure — sampled as
  `dreamTex` (**dream-as-texture**, same seam as `spikes/dream-as-texture`);
- the **wind is procedural in the shader**, driven by `windDir`/`gust` uniforms, so it can
  actually redirect live. A baked loop bakes ONE wind direction — exactly ADR-0009's
  argument against video carrying the signal;
- **idle stays byte-identical** — no signal ⇒ neutral wind ⇒ the field resolves to its
  resting sway; nothing jumps.

So interactivity is a **uniform, not a new ambient surface** (design-0023 decision 7).

## Uniform contract

The new sibling signal (alongside the shared `{state,busy,warm,snag}` agent contract):

| uniform | type | meaning | producer source (design-0023 §Interactivity) |
|---|---|---|---|
| `windDir` | `vec2` | direction the field **bows**; `(0,0)` = neutral | `f(window-drag.θ)` — last drag vector |
| `gust` | `float` 0..1 | extra bend amplitude + ripple | `g(window-drag.speed)` — drag speed |
| `uWindReact` | `float` 0..1 | master gate (config); `0` ⇒ wind signal fully inert | config |
| `uDreamMix` | `float` 0..1 | `0` procedural look (fallback) ↔ `1` dream-as-texture | config / asset presence |
| `uReducedMotion` | `float` 0..1 | **prefers-reduced-motion** damp: stills gust/ripple/parallax (ADR-0023 P2.12) | OS a11y setting |
| `uStale` | `float` 0..1 | **producer-dead / stale-feed** grade — a DISTINCT cool desat look, never serene idle (ADR-0023 P2.12) | consumer liveness watchdog |
| `uAgentState` `uAgentBusy` `uAgentWarm` `uAgentSnag` | int/float | the shared grammar (idle/working/needs_you/snag) | `feed.rs` → `agent.json` |

## Painterly as STRUCTURE, not a post-filter (ADR-0023 P2.9)

The art-director scored the prior look **4/10** — "default game-grass + a posterize filter,"
not an oil painting. The fix is to push the painterly direction into the *rendering*, per the
references the art-director set (David Holland's [BotW/Totoro UE4 meadow](https://80.lv/articles/meadows-creating-stylized-nature-in-ue4)
+ the [80.lv Ghibli-in-Blender](https://80.lv/articles/creating-ghibli-inspired-painterly-environment-in-blender)
breakdowns). Both teach the same STRUCTURAL recipe — *not* a post-pass:

- **1–2-tone colormaps, color in FLATTENED REGIONS.** A low-frequency blurred-noise field
  (`regBig`) quantises the field into a few painted color *zones*; tip-weighted blade **clumps**
  (per-6-blade `clump` id) inherit one region color + a single quantised 2-tone light/shadow
  level. Color varies in **patches**, never per-blade. (The old per-blade `jitter`/`brush`
  high-freq scatter under a global palette-snap was exactly the "posterize filter" tell.)
- **"Removed all but the slightest roughness"** — the in-region tone is a 2-LEVEL step
  (`step(0.5,tone)`), so patches read FLAT; no per-pixel brightness scatter.
- **The green note `#9bb04a` is a COOL REGION, not a tint** — its band is the narrowest and its
  saturation is pulled toward amber, so it reads as a cool *note within the field* (region
  contrast, "contrast not dominance"), never a uniform green wash.
- **Directional brush grain aligned to the wind** — the canvas grain is sampled along the wind
  axis (`brushDir`), so the strokes lean with a redirect (at idle it falls back to a fixed
  vertical grain — identity-safe).
- **Scumbled 2-tone sky** (the old flat gradient was the most generic element) — two palette
  tones scumbled into soft cloud bands broken by low-freq noise, with the green note cooling the
  horizon. Painted cloud SHAPES, not a smooth ramp.

## Real dreamTex (ADR-0023 P2.10)

`dreamTex` is no longer a flat-gradient stand-in. The harness loads the **real Blender EEVEE
graded frame** `../creative-env/out/graded/g_0001.png` (read-only; creative-env is owned by
another lane and is **not modified here**). If the frame is absent the harness falls back to the
on-palette gradient so the seam still compiles. `grass_dream_left.png` (`uDreamMix=1`) is the
demo still — it now carries the actual painterly render, wind-warped.

## a11y / liveness (ADR-0023 P2.12)

- **`uReducedMotion`** damps the gust amplitude, the ripple, and the region parallax toward a
  near-still field (the neutral baseSway is barely touched — calm-rest is preserved). `live.qml`
  toggles it with **R**. At idle there is no signal motion to damp, so it is identity vs the
  all-zero baseline (proven below).
- **`uStale`** is a DISTINCT producer-dead look: a cool desaturated wash + a soft vignette, so
  "I can't read the desktop" can never masquerade as serene idle. Cool (toward the sky's
  olive-grey mean), **never warm and never red** — warmth stays reserved for `needs_you`.

**Mapping is the brief's, and deterministic** (design-0023): the model proposes the *binding
intent* (`bindings: { "wind.direction": "window-drag.vector", "wind.gust":
"window-drag.speed" }`); **code disposes** the pure mapping. No model in the live loop.

### How the window-drag producer feeds it (design-0023 §Interactivity)

```
KWin script (kwin-mcp / computer-use lane)        QML poller (the consumer)
  observes window-move geometry deltas      ──▶   reads drag vector beside agent.json,
  writes (θ, speed) to a runtime file             low-passes into windDir/gust uniforms
  beside agent.json (versioned)                   (the feed.rs 2–20 s spring, NOT raw edges)
```

**Calm constraint (load-bearing).** The signal is **low-passed through the `feed.rs` spring**
— a drag *nudges* the wind and it eases back, staying below the attention-capture threshold.
Bounded ranges, no abrupt onset. The low-pass belongs in the **QML poller**, never in raw
uniform reads (and **not via XHR** — `qml6` sync XMLHttpRequest silently left uniforms at 0
in the hills-reactive spike). This harness uses a generated `wind_data.js` shim only to load
deterministically under a bare `qml6`.

## The idle byte-identical contract (proven)

Every signal term is a **guarded additive that collapses to identity at 0** — no
constant-term leak. The neutral resting sway reads **none** of the signal terms, so the rest
state is independent of the signal entirely. Two checks, both pass at the harness level:

1. **Determinism** — re-rendering `idle` at fixed `iTime` is **bit-identical** (same
   SHA256, `maxΔ=0`).
2. **No leak** — flipping `uWindReact` `0→1` while `windDir=(0,0)`, `gust=0` changes
   **zero pixels** (`maxΔ=0`). The wind signal contributes exactly nothing at neutral.

(There is no separate "unmodified baseline `.qsb`" to diff against the way hills-reactive
has — this field is net-new geometry — so the gate-independence check is the equivalent
proof that the additives carry no constant term.)

## Grammar reuse (don't reinvent — ADR-0001)

Mirrors the committed Hills/Flow shader + `spikes/dream-as-texture` exactly:

- **palette LOCKED** to the brief's `#b8862f #e3c46a #f4e3a1 #7d5e22 #9bb04a`
  (ambers + a green base note); painterly = code-side **palette-reduce toward the locked
  swatches + canvas grain** (a post-grade, not a prompt word — ADR-0009 mandatory grade).
- **working** → motion `pace` + a capped multiplicative brightness lift (identity at 0).
- **needs_you** → the **ONE warm source**, additive dawn glow `(1.00,0.60,0.34)` under a
  **highlight guard** (`headroom`) so working + warm can't compound past white.
- **snag** → desaturate + dim, **never red**.

## Run

On the **live session** (offscreen has no GL context here ⇒ blank frames):

```bash
./run.sh          # recompiles the .qsb (qsb lives off-PATH at /usr/lib/qt6/bin/qsb),
                  # then renders every wind/state frame to grass_<name>.png
# recompile by hand:
/usr/lib/qt6/bin/qsb --qt6 -o windable_grass.frag.qsb windable_grass.frag
# render one config by editing wind_data.js, then: qml6 harness.qml  (writes out.png)
```

## What was rendered (evidence) vs not

**Rendered + verified on-session** (`qml6` 6.11.1, fixed `iTime=8.0` so frames differ ONLY
by the signal):

| capture | config | what it shows |
|---|---|---|
| `grass_idle.png` | all-zero | flattened amber color regions, scumbled sky, neutral sway — the rest state |
| `grass_wind_left.png` | `windDir=(-0.9,0.05)` | blades **bow left**; brush grain leans left |
| `grass_wind_right.png` | `windDir=(0.9,0.05)` | blades **bow right** (redirect is the only change) |
| `grass_wind_gust.png` | `gust=0.9` diagonal | strong bend + gust ripple |
| `grass_wind_working.png` | wind + `busy=0.85` | wind under working pace/brightness |
| `grass_wind_needsyou.png` | wind + `warm=0.9` | the ONE warm glow, low-centre, capped |
| `grass_wind_snag.png` | wind + `snag=0.9` | desaturated + dimmer, never red |
| `grass_dream_left.png` | `uDreamMix=1` | **real EEVEE `g_0001.png`** dream-as-texture, wind-warped |
| `grass_reduced_motion.png` | gust `0.85` + `reducedMotion=1` | same gust as `wind_gust` but ripple/parallax **damped** |
| `grass_stale.png` | `stale=1` | producer DEAD — cool desat + vignette, **distinct from idle** |

Pixel diffs (PIL/numpy, on-session) confirm the invariants AND the distinctness:

| diff | maxΔ | px changed | meaning |
|---|---|---|---|
| idle re-render | **0** | **0** | bit-identical (same SHA256) |
| idle vs `uWindReact=1` | **0** | **0** | wind gate carries no constant term |
| idle vs `uReducedMotion=1` | **0** | **0** | the new damp is identity at idle (no leak) |
| idle vs **stale** | 115 | all 589824 | stale is a genuinely DISTINCT look |
| `wind_gust` vs `reduced_motion` | 87 | 143758 | reduced-motion measurably damps signal motion |
| `wind_left` vs `wind_right` | 85 | 234376 | the redirect (embodiment wedge) works |

**Honestly NOT done here:**

- **No live producer.** The window-drag → `(θ,speed)` KWin script does not exist (design-0023
  open question 5 + ADR-0023-to-be). This spike fakes the signal via `wind_data.js`. The
  real consumer must be a **QML poller that low-passes** into the uniforms.
- **The low-pass spring is not exercised** — captures are single static frames, so the
  ease-in/ease-back behaviour (the calm constraint) is asserted by design, not demonstrated.
- **Not measured** — no frame-time / VRAM numbers. The painterly pass ADDED noise calls
  (region fbm + a finer octave + tone fbm + directional brush fbm ≈ 5 fbm evals/px vs the
  prior 3); still 2 octaves each and cheap, but `design-technologist` /
  `rust-performance-reviewer` own the real cost call before this leaves spike — measure with
  the signal uniforms active (the parallax/brush terms only cost when wind is non-zero).

## Gaps for the design-council

- **`art-director`** — the P2.9 structural pass landed: color now lives in flattened regions
  (not per-blade scatter), the sky is scumbled 2-tone, the green note is a cool region not a
  tint, brush grain is wind-aligned. Open call: does `uDreamMix=0` (procedural) now read as
  *painting* well enough to ship as the always-on legible path, with the real EEVEE
  `dreamTex` (now wired, `grass_dream_left.png`) as the richer on-demand look?
- **`motion-designer`** — owns the wind's easing/cadence + the spring time-constants; the
  bend curve here (tip-weighted bow + gust ripple) is a placeholder for the motion language.
- **`design-technologist`** — feasibility + cost; wire the real EEVEE render into `dreamTex`
  and build the QML poller / low-pass.
- **`ambient-embodiment-reviewer`** — confirm the idle invariant proof is sufficient
  (gate-independence vs a baseline-`.qsb` diff) and that one warm source / no-red holds.
- **`rust-performance-reviewer`** — fbm octave count + per-pixel palette loop against the
  yield/VRAM budget, with the signal uniforms active.
- **The window-drag producer** (design-0023 OQ-5) is the real gating dep: per-window vs
  aggregate, the Wayland KWin live-move-delta API, and the privacy posture (**geometry
  deltas only, never window content**).
```
