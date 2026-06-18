# Design 0023 — Prompt → themed environment → tracked path (creative pipeline)

The missing layer above ADR-0022. ADR-0022 + Design-0022 made Blender (and now an Unreal
editor-automation lane) **run safely under the lease** — that is the *floor*. This is the spec for the
*authoring workflow* that floor was built to carry: **a text prompt becomes a coherent, artistically
themed 3D environment with a tracked path through it**, rendered as a reversible artifact.

**Status (2026-06-18): Proposed — design only, nothing built.** This is a *design spec*; it proposes
six decisions that want ratifying as **ADR-0023** before code (ADR-before-code discipline, like ADR-0009).
The substrate it stands on is real and verified (ADR-0022 lease, Design-0022 `AdoptScope`, the live
Blender forge + the UE 5.8 editor-automation lane); the creative pipeline below it is **not written**.

## Where this sits in the decision graph (the load-bearing reframe)

This is **not a new primary ambient surface** — that would breach [ADR-0009](../adr/0009-dreaming-shader-primary-video-as-texture.md),
which fixed the procedural shader as the permanent renderer of agent state and put video/3D behind it as
*texture-input + on-demand*. ADR-0009 already reserved the slot: *"3D is still deferred, not dropped — it
slots in behind the same `agent.json` seam and the same coordinator lease when local 3D generation
matures."* This pipeline **is** that slot maturing. So its output is:

- an **ADR-0009 Surface-B "generate" artifact** — the user explicitly asks for an environment; completion
  is notification-as-control (Preview / Keep / Discard), never an auto-applied desktop change; and
- a **lucid anchor source** — the rendered camera move (or its frames) seeds the dreaming tree at the
  existing seam: `lucid_engine.py start <session> --image <png>` (`lucid_engine.py:269`,
  [ADR-0014](../adr/0014-lucid-interactive-branching-dream-loop.md)/[0015](../adr/0015-lucid-mvp-linear-chain-through-the-lease.md)).

It is **never** the ambient wallpaper directly; if a walkthrough ever drives the desktop it does so as a
texture the shader warps, through the ADR-0005 apply/rollback tx — out of scope here.

## The non-negotiable that shapes the whole pipeline

**The model proposes; code disposes.** A model that freely emits geometry, materials, and a camera flight
gives you incoherence and clipping — the two failure modes the user's phrasing ("*coherent*", "*tracked
path through*") names directly. So the model never mutates the scene. It emits one validated artifact —
**the brief** — and code builds everything from it deterministically. The payoff is structural: because
**code** places the geometry, code holds ground-truth on every element's bounds and the free space between
them, which is exactly what makes both *theming coherence* and a *collision-free camera path* cheap,
deterministic, and checkable. Coherence and the tracked path are not things we hope the model gets right;
they are deterministic disposal gates.

## The pipeline

```
prompt ─▶ [0] brief (model proposes)        the theme contract — a validated schema, the single
                  │                          source of coherence; every later stage reads only this
                  ▼
          [1] geometry source               procedural blockout (default) | local-gen hero props (deferred)
                  │
                  ▼
          [2] assemble + theme (code)        place elements, clamp palette, rig lighting — Blender bpy
                  │                          (default) or UE editor_run_python (walkthrough target)
                  ▼
          [3] lay the path (code)            ONE spline = visible route motif + camera rail (dual-purpose)
                  │
                  ▼
          [4] validate (code, deterministic) bounds + clip raycast along the spline; nudge or regen
                  │
                  ▼
          [5] render the move ─▶ artifact    EEVEE mp4/frames ─▶ lucid `start --image` | Surface-B notif
```

### [0] The brief — the theme contract

The model's *only* output. A constrained, validated schema (enums, not freeform), e.g.:

```jsonc
{
  "theme":   "drowned cathedral, bioluminescent",   // free text, prompt-derived
  "mood":    "reverent | calm",                       // maps to lighting + motion rate
  "palette": ["#0a1f2e", "#1c4b5a", "#5fd3c0", "#e8f6f2"],  // LOCKED — code clamps every material to it
  "elements": [                                        // what to place; code chooses exact transforms
    { "id": "nave",     "kind": "vault",   "scale": "hero",   "count": 1 },
    { "id": "pillars",  "kind": "column",  "scale": "large",  "count": 8, "layout": "avenue" },
    { "id": "motes",    "kind": "particle","scale": "small",  "count": 200 }
  ],
  "lighting": { "key": "underwater-god-rays", "intensity": "low" },
  "camera": {                                          // INTENT only — code lays the actual spline
    "move": "reveal", "subject": "nave", "arc": "rising",
    "duration_s": 12, "easing": "ease-in-out"
  },
  "path": { "render_as": "stepping-stones", "from": "entrance", "to": "nave" }
}
```

`palette` is **load-bearing**: code clamps every generated material's albedo/emission to the locked set
(nearest-in-palette), so coherence holds even if a downstream gen step (Phase 2) returns off-theme
textures. `elements` say *what*, not *where*; code computes transforms. `camera.move` and `path` are a
fixed enum vocabulary (`reveal | push-in | orbit | fly-through | drift`), not a free camera script.

### [1] Geometry source — procedural blockout first

| Source | VRAM | Determinism | Fit | Verdict |
|---|---|---|---|---|
| **Procedural `bpy` blockout** (primitives, arrays, modifiers) | ~0 extra | full | calm/abstract/architectural — ADR-0009's aesthetic | **default** |
| **Local gen** (Hunyuan3D / TripoSR via the ComfyUI lease) for hero props | heavy, lease-gated | low (model output) | photoreal/organic hero assets | **deferred** (Phase 2) |
| Cloud gen (Rodin/Meshy/…) | n/a | n/a | — | **rejected** — local-first; already refused in [research-0012](../research/0012-creative-app-mcp-blender-unreal.md) |

Procedural blockout is deterministic, needs no extra VRAM model alongside resident ComfyUI (the
[ADR-0018](../adr/0018-vram-coexistence-budget-partition.md) headroom reality), and *is* the calm/abstract
look ADR-0022 §6 already says EEVEE covers. Gen-from-prompt hero props are a real want but they ride behind
the [ADR-0022 §4](../adr/0022-creative-app-mcp-blender-unreal.md) autonomous-surface hardening and a curated
local model — out until that lands.

### [2] Assemble + theme — Blender default, Unreal optional walkthrough

| | **Blender EEVEE** (default) | **Unreal 5.8** (optional walkthrough) |
|---|---|---|
| Scripting | `bpy` over the forge MCP — deterministic | `editor_run_python` (verified live, ADR-0022 v2.3) |
| Lease | `AdoptScope` (Design-0022, **done + verified**) | `Spawn`/process-group profile (**design-intent, not built**) |
| Camera rail | Bezier curve + Follow-Path constraint | Sequencer + CineCameraActor on a spline (first-class) |
| Assets | procedural `bpy`; glTF import native | **PCG** (procedural, default-on, Linux) + **Interchange/glTF** import via `AssetImportTask` — no custom bridge |
| Render out | EEVEE → mp4/frames → lucid seam ✅ | **rasterizer/viewport capture only** — no Linux MRQ/Path-Tracer (ADR-0022 §8) |
| VRAM | light, lease-proven | 3–16 GB, collides with resident ComfyUI |
| Fit | calm/abstract, texture output — ADR-0009 ✅ | real-time Lumen GI, interactive feel |

**Recommendation: Blender EEVEE is the deliverable renderer; Unreal is the "feel it in real-time / richer
GI preview" target, not the output path — until the §8 render gate flips.** They share the **brief, not
assets** — each builds its own scene from the same theme contract via its own scripting — which keeps both
coherent (same locked brief) **and means there is no custom Blender→UE asset bridge to build**; the
off-the-shelf path covers it (see *Asset ingestion*). UE's Sequencer is genuinely the better camera-path
tool, so the walkthrough target earns its place for *previewing the move* (and, below, for **live
interactivity**); you just can't get a film-quality frame *out* of it on Linux yet, so the artifact comes
from EEVEE.

### [3] Lay the path — one spline, two jobs

The user wants "a *tracked path through* the environment." Make the **path a first-class scene element**:
code lays a single spline from `path.from` to `path.to` threaded through the layout's negative space, and
that one spline serves double duty —

1. the **visible route motif** (`path.render_as`: stepping-stones / a glowing trail / a road, themed), and
2. the **camera rail** (the tracking shot follows it, framing `camera.subject` per `camera.arc`).

Authoring it once means the thing you *see* and the thing the camera *rides* are guaranteed to agree — the
tracking shot is literally a move *along the visible path through the space*. `camera.move` shapes how the
camera sits on the rail (a `reveal` starts wide and rises; an `orbit` arcs; a `fly-through` hugs the spline).

### [4] Validate — the determinism gate

Because code placed every element, code raycasts. Sample the spline at N stations; at each, check the
camera position isn't inside any element's bounds and the look-at to `subject` isn't occluded. A station
that fails is **nudged deterministically** (push off the nearest surface normal) or, if unrecoverable, the
spline is **regenerated** with more clearance. A "tracked path" that clips through a pillar is the failure
mode; this gate is what makes the path *tracked* and not *random*. No model in this loop — pure geometry.

### [5] Render + reversibility

EEVEE renders the move to mp4/frames under the `AdoptScope` lease. The artifact is **a reviewable proposal,
never an auto-applied change** ([ADR-0019](../adr/0019-reviewable-request-queue.md) queue + ADR-0009
Surface-B notification-as-control). The **brief + the generated `.blend`** are saved alongside it, so the
environment is reproducible and the whole thing is revertible by construction (delete the artifact; nothing
on the desktop moved). Frames can seed the lucid tree (`start --image`) for an animated/branching dream
continuation. Fail-open throughout (ADR-0003): gen fails → fall back to a simpler procedural blockout or a
still; never wedge, never half-apply.

## Interactivity — live desktop-signal bindings

(requirement added 2026-06-18) The scenes aren't only rendered films — they take **light live interactivity
from the desktop**. The canonical binding: **the wind direction follows the direction a window is dragged**
(drag speed → gust strength). This is the AgentOS embodiment vision applied to the creative scene — the
environment *is* the desktop's state made ambient — so it must land **inside** that grammar, not invent a
new one.

**Where it lives (the ADR-0009 reconciliation).** A live-reactive scene sounds like it breaches ADR-0009
(the procedural shader is the permanent live renderer of state; 3D/video is texture/on-demand). It doesn't —
if we route it the way ADR-0009 already routes generated video: **the rendered grass field supplies the
*look* (palette + painterly style) as a texture; the procedural shader layer carries the live wind uniform
and bends the grass** (dream-as-texture, ADR-0009 Surface A). So:

- the baked render carries the *look*, **not the motion**;
- the **wind is procedural in the shader**, driven by a `windDir`/`gust` uniform, so it can actually
  redirect live — a baked loop bakes ONE wind direction, which is exactly ADR-0009's argument against
  video carrying the signal;
- **idle stays byte-identical** (ADR-0009's contract): no signal → neutral wind → the layer resolves to its
  resting motion; nothing on the desktop is touched.

This keeps the always-on ambient renderer the shader (ADR-0009 untouched) and makes interactivity a
**uniform, not a new surface**.

**The grammar (don't reinvent — ADR-0001).** This is the exact producer→consumer shape the substrate
already runs for the reactive wallpaper: `feed.rs` → `agent.json` → shader uniforms. Add a sibling signal:

- **Producer (owed):** a **KWin script** (the kwin-mcp / computer-use lane) observes window-move geometry
  deltas and writes a drag **vector** (θ, speed) to a runtime file beside `agent.json` (versioned, per the
  ADR-0009 two-consumer lesson).
- **Consumer:** the scene shader reads `windDir = f(drag.θ)`, `gust = g(drag.speed)`.
- **The mapping is the brief's, and deterministic.** The model proposes the *binding intent*
  (`bindings: { "wind.direction": "window-drag.vector" }`); **code disposes** the pure mapping. No model in
  the live loop.

**Calm constraint (load-bearing).** Wind that snaps to every drag is an attention magnet — the precise thing
the ambient vision forbids. So the signal is **low-passed by the consumer-side spring** (a first-order ease at
ω≈1.5 rad/s, in the QML consumer and the Rust wind sink — `feed.rs` is edge-driven and stateless, NOT a temporal
damper; the smoothing has always lived in the consumer): a drag *nudges* the wind and it eases back,
staying below the attention-capture threshold. Bounded ranges; no abrupt onset.

**Safety / fail-open.** Pure-function binding, an **allowlist of `signal → target` pairs** (validated like
the brief enums), bounded output. No producer / stale signal → neutral wind (ADR-0003). Reversible by
construction — it's a uniform; nothing is persisted to desktop state.

**Consequence — interactivity re-weights the real-time path.** Live reaction is the one thing the EEVEE
*artifact* can't do (it's pre-baked); it belongs to a **real-time renderer** — the shader layer (default,
ADR-0009 grammar) or the UE walkthrough target (which earns its keep here: real-time Lumen + PCG, both
Linux-native). The **rendered film** (camera tracking the mown trail) and the **live ambient scene**
(near-still camera, windable, desktop-reactive) are **two modes of one brief**, not two scenes.

## Asset ingestion — settled (no custom bridge)

Researched 2026-06-18 against the local UE **5.8.0** Linux install. The "Blender→UE asset bridge" earlier
listed as deferred **does not need building** — the off-the-shelf path is already on disk, Linux-native, and
Python-scriptable (sources: Epic Interchange/PCG/USD docs, Fab EULA; plugins verified present + enabled):

- **Procedural in-engine → PCG.** Production-ready since 5.7, **enabled by default**, Linux-native, driven
  from `editor_run_python`. The right tool for the grass field *if built in UE*; no import in the loop, and
  instanced foliage rendering keeps VRAM tunable on the shared 4090.
- **Model import → Interchange + glTF/GLB** via `unreal.AssetImportTask`. The glTF translator (`GLTFCore`)
  ships **compiled for Linux** and on by default; Blender's glTF exporter is first-class, so the round-trip
  is the cleanest PBR of any format, at near-zero steady VRAM. **Make glTF/GLB the house interchange format**
  (OBJ for trivial geometry; OpenUSD — present, Linux-allowlisted, off by default — when scenes need
  *composition*).
- **Drive it all through `editor_run_python`.** `runreal/unreal-mcp` exposes **no import tool**; import and
  PCG both ride the one Python channel. Keep Remote Execution local-only + lease-gated (existing README
  discipline — it's arbitrary code in the editor).
- **Megascans/Fab — human-in-the-loop only.** No public download API (promised, unshipped), GUI-bound, the
  free-claim window closed 2024-12-31, the Standard License is silent on programmatic ingestion, and the
  assets are VRAM-heavy. A human acquires a file once via Fab; *then* the glTF/Interchange path ingests it.
  **Do not design an automated Fab fetch** (local-first + cloud + licensing strikes).
- **The render gate is unchanged.** Everything above is *import/generation* and works on Linux; the
  Windows/DX12-only limit is **Path-Tracer / film-quality MRQ** (ADR-0022 §8) — a *render* gate, not an
  *import* gate. Getting models *in* ≠ rendering beauty frames *out*. EEVEE stays the deliverable renderer.

## Canonical first sample — the amber field

When it comes time to generate the first sample, the scene is **a waving amber field of grass, stylized
like an oil painting**. Its brief:

```jsonc
{
  "theme":   "a waving amber field of grass, stylized like an oil painting",
  "mood":    "calm | pastoral",
  "palette": ["#b8862f", "#e3c46a", "#f4e3a1", "#7d5e22", "#9bb04a"],   // ambers + a green base note
  "elements": [
    { "id": "field",   "kind": "grass-instances", "scale": "field", "count": 50000, "layout": "scatter" },
    { "id": "horizon", "kind": "ground-plane",     "scale": "hero",  "count": 1 }
  ],
  "lighting": { "key": "golden-hour-low-sun", "intensity": "low" },
  "motion":   { "field": "wind-wave", "speed": "slow" },                // the "waving"
  "camera":   { "move": "drift", "subject": "horizon", "arc": "level", "duration_s": 14, "easing": "ease-in-out" },
  "path":     { "render_as": "mown-trail", "from": "foreground", "to": "horizon" },
  "render":   { "style": "painterly" },                                 // a code post-grade, NOT a prompt word
  "bindings": { "wind.direction": "window-drag.vector", "wind.gust": "window-drag.speed" }  // live
}
```

It is the right *first* sample precisely because **it is procedural by nature and needs zero imported
models** — grass is instanced scatter, the trail is a mown swath, the painterly look is a post-grade. So it
exercises the whole Phase-0 loop (brief → `bpy` scatter → palette-clamp → path-spline → validator → EEVEE
move → artifact) **without touching the deferred asset bridge or any gen step** — exactly the part we want
proven first. Three notes:

- **"waving"** → `motion.field: wind-wave`, a deterministic vertex/wind animation the camera renders over
  `duration_s`; not a per-frame model decision.
- **"stylized like a painting"** → enforced as a **system-owned post-grade** (Kuwahara / brush-stroke +
  palette reduction + canvas grain), echoing ADR-0009's mandatory post-grade: code disposes the style, the
  model never prompt-words it, and the same parametric grade is reproducible and revertible.
- **the mown trail is the dual-purpose spline** ([3]) — visible motif *and* camera rail — so the `drift`
  is a move *along the path through the field*, which is the whole "tracked path through the environment"
  promise in one shot.
- **`bindings` makes the field live** (Interactivity): in the ambient mode the procedural wind takes its
  direction from the last window-drag vector (eased), so dragging a window left makes the field bow left —
  the desktop's own motion becomes the weather. In the *film* artifact the binding is inert (pre-baked).

## What's reachable now vs deferred

| Phase | Scope | Blocking deps | Reachable? |
|---|---|---|---|
| **0 — Blender spike** | prompt → brief → `bpy` blockout + palette-clamp + path-spline + validator → EEVEE move → lucid/Surface-B | all **green** (forge MCP, `AdoptScope`, EEVEE, lucid seam) | **yes, today** |
| **1 — UE walkthrough** | same brief → `editor_run_python` blockout + Sequencer spline camera → rasterizer capture | UE `Spawn` **lease profile** (design-intent → build) | near-term |
| **2 — gen + film** | local-gen hero props (Hunyuan3D via ComfyUI lease); UE MRQ film render | ADR-0022 §4 hardening; the §8 Linux render gate | deferred |

*(Asset ingestion is **not** a phase — model import via Interchange/glTF and in-engine PCG are off-the-shelf
and available now; see Asset ingestion.)*

The honest read: **Phase 0 is a real end-to-end loop buildable now on verified parts** — it just trades
photoreal gen for procedural blockout and ships the calm/abstract aesthetic ADR-0009/0022 already chose.
The "anything from a prompt" generality (arbitrary photoreal environments, UE-quality film renders) lives
in Phase 2 behind durable gates, not laziness. **The interactive (live) mode is a parallel track, not a
later phase:** it rides the existing reactive-shader grammar plus one new **KWin window-drag producer** —
reachable as soon as that producer lands, independent of the render phases (see Interactivity).

## Decisions this asks ADR-0023 to ratify

1. The creative-environment pipeline is an **ADR-0009 Surface-B artifact + lucid anchor source**, not a new
   primary ambient surface.
2. **Coherence = a locked brief (theme contract) + deterministic code disposal** (palette clamp,
   code-placed geometry), never trusted model freeform.
3. **Procedural blockout first**; local-gen hero assets deferred behind ADR-0022 §4.
4. **Blender EEVEE is the deliverable renderer; Unreal is an optional real-time walkthrough target;** they
   **share the brief, not assets.**
5. **The path is one dual-purpose spline** (visible motif + camera rail); the camera move is deterministic
   disposal of model-proposed *intent*, gated by a **bounds/clip validator**.
6. **Lease-gated, fail-open, reversible** end to end — output is a reviewable proposal (ADR-0019), the brief
   + `.blend` are saved for reproducibility.
7. **Light interactivity is a live shader uniform, not a new surface.** Desktop signals (canonically a
   window-drag vector → wind direction/gust) drive the **dream-as-texture** layer (ADR-0009-compliant — the
   render is the *look*, the shader carries the live *motion*), **low-passed by the consumer-side spring** (not `feed.rs`, which is edge-driven),
   model-proposes/code-disposes, fail-open to neutral, idle byte-identical.
8. **Asset ingestion is off-the-shelf — no custom bridge.** PCG (in-engine procedural) and Interchange+glTF
   (model import) — both Linux-native + Python-scriptable; Megascans/Fab human-in-the-loop only; the §8
   render gate (Linux import yes, film render no) is unchanged.

## Open questions (follow-ups, not blockers)

1. **Brief schema versioning.** Like `agent.json` (ADR-0009 consequence), the brief is a contract between
   the proposer and the disposer — version it before a second consumer (UE) reads it, so a schema change
   can't silently desync Blender and UE builds.
2. **Camera vocabulary breadth.** Is the fixed `move` enum enough, or does the user want keyframe-able
   waypoints (a richer, harder-to-validate authoring surface)? Start enum-only; revisit.
3. **N-element layout solver.** Avenue / radial / scatter layouts from `elements[].layout` — how much
   solver before it's its own design step. Phase 0 ships 2–3 hardcoded layouts.
4. **When does Phase 1's UE lease profile land?** It's the gating dep for the walkthrough target and is the
   open item already flagged in the `integrations/unreal/` README (a `Spawn`/process-group reclaim, *not*
   `AdoptScope`, since UE isn't a flatpak scope).
5. **The window-drag producer.** A KWin script (kwin-mcp / computer-use lane) must emit the drag vector to a
   runtime file beside `agent.json` (versioned). Open: per-window vs aggregate, the Wayland KWin-scripting
   API for live move deltas, and the privacy posture — **geometry deltas only, never window content** (owe a
   note to the privacy-skeptic + wayland-computeruse-reviewer).
6. **Bindable-signal vocabulary.** Which desktop signals are bindable (window-drag, workspace switch, focus,
   time-of-day) and the allowlist that validates a brief's `bindings`. Start window-drag-only.
</content>
</invoke>
