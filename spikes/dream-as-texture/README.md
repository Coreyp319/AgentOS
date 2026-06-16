# spike: dream-as-texture

Throwaway. The procedural counter-direction to ADR-0008's "video loop keyed by
discretized agent state". One claim, made testable:

> Generated video and frame-by-frame float responsiveness are **not** in tension.
> You keep both if the video is the **substrate the procedural layer warps**
> (a `sampler2D dreamTex`), not the **output** (a clip you pick by state).

`dream_field.frag` samples a generated clip as an evolving colour/structure field,
then expresses the live `{busy,warm,snag}` floats on top of it with the SAME
grammar the committed Hills/Flow shader uses (`spikes/hills-reactive/aurora.frag`):

- **working** → a procedural domain-warp of the dream + a capped brightness lift
  (`pace`, `*1.08`). The dream never changes clip; the *warp* scales with `busy`.
- **needs_you** → the one warm dawn source, additive under a **highlight guard**
  (`headroom`), localised low+centre. Same RGB `(1.00,0.60,0.34)` as the committed grammar.
- **snag** → desaturate + dim, never red.

## Why this answers the panel's Tier-2 blocker

The scorecard (`docs/research/0002-dreaming-panel-scorecard.md`, item #6) says the
continuous floats "cannot survive discretization into a few loops". This spike's
floats never get discretized — they drive *continuous* uniforms exactly as
`feed.rs:88-97` emits them (ramped 0.7→1.0 etc.). The video is demoted to *texture*,
not the grammar to *fallback*.

## The invariant, provable

At `uAgentReact=0` (or all-of busy/warm/snag = 0) **every agent term collapses to
identity**: `warpAmt=0`, `*1.0`, `+0`, `mix(...,0)`. So `idle` == "the dream played
untouched". That is the byte-identical-at-rest contract (`vision.md:93`) preserved —
the loop-at-idle screensaver problem the reviewer named does NOT appear, because at
idle the procedural layer is a pass-through, and *what* the dream is at idle is a
config choice (a still frame, or nothing, keeps true rest).

## Run (live session — offscreen has no GL context, see hills-reactive/README)

    qsb --qt6 -o dream_field.frag.qsb dream_field.frag
    # harness: a ShaderEffect with `dreamTex` = ShaderEffectSource of a VideoOutput
    # (or a still for a static check). Sweep uAgentBusy/Warm/Snag 0..1, pin iTime.

## What this spike does NOT claim

- It does not solve VRAM eviction — that's the substrate's job (scorecard Tier-0 #2,
  `main.rs:16`), and `dream-as-texture` actually *reduces* the stakes: a single short
  cached clip as a texture is far cheaper to hold than a per-state library of loops,
  and the procedural layer degrades to pure-shader (no `dreamTex`) for free.
- It does not own the motion language — `motion-designer` owns the warp's easing/cadence.
- Feasibility/cost measurement is `design-technologist`'s call.
