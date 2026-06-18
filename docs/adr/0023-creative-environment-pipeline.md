# ADR-0023: Creative-environment pipeline — prompt → themed 3D environment with a tracked path, as a reversible artifact

- Status: Proposed
- Date: 2026-06-18
- Relates to: ADR-0001 (substrate not orchestrator — don't reinvent the producer→consumer
  grammar), ADR-0003 (fail-open supervised), ADR-0005 (apply/rollback tx — any desktop swap),
  ADR-0009 (dreaming: procedural shader primary, 3D/video as *texture* + on-demand),
  [ADR-0014](0014-lucid-interactive-branching-dream-loop.md)/[0015](0015-lucid-mvp-linear-chain-through-the-lease.md)
  (lucid — the rendered move seeds the dream tree at the `--image` seam), ADR-0019 (reviewable
  request queue — output is a held proposal), ADR-0022 (creative-app MCP — the lease floor this
  authoring layer stands on)
- Spec: [`docs/design/0023-creative-environment-pipeline.md`](../design/0023-creative-environment-pipeline.md)
  (the full pipeline; this ADR ratifies, it does not restate)
- Does NOT supersede ADR-0009. It **fulfills** ADR-0009's reserved slot — *"3D is still deferred,
  not dropped — it slots in behind the same `agent.json` seam and the same coordinator lease when
  local 3D generation matures."* This pipeline is that slot maturing, inside ADR-0009's grammar.

## Context

ADR-0022 (and Design-0022) brought the live Nimbus Blender forge — and now a UE 5.8 editor-automation
lane — **under the lease**: admit-before-launch is in, the `AdoptScope` cgroup-reclaim path is built,
reviewed, and verified against a throwaway scope. That is the *floor* — the substrate's job, done. But
the floor was built to carry something, and the thing it carries is **not written**: the creative
*authoring* layer above it.

The goal this ADR ratifies: **a text prompt becomes a coherent, artistically themed 3D environment
with a tracked path through it, rendered as a reversible artifact.** The user's two load-bearing words
— *coherent* and *tracked path through* — name the two failure modes of a model that freely emits
geometry, materials, and a camera flight: incoherence and clipping. The whole pipeline is shaped to
make both impossible by construction rather than hoped-for.

A requirement was added late (2026-06-18): the scenes are not only rendered films — they take **light
live interactivity** from the desktop, canonically *the wind direction follows the direction a window
is dragged* (drag speed → gust strength). This must land *inside* the ambient grammar (the agent *is*
the environment), not invent a new surface — which is the tension this ADR has to reconcile against
ADR-0009, and does (Decision 7).

## Decision

Ratify the eight decisions specified in Design-0023; nothing here re-specifies them — see the spec.

1. The creative-environment pipeline is an **ADR-0009 Surface-B artifact + lucid anchor source** — an
   explicitly-requested "generate" output, not a new primary ambient surface. The wallpaper renderer
   stays the procedural shader (ADR-0009 untouched).

2. **Coherence is a locked brief plus deterministic code disposal, never trusted model freeform.** The
   model proposes exactly one validated artifact — the *brief* (a theme contract: enums, a locked
   palette, elements-as-*what*-not-*where*) — and code disposes everything from it: code places the
   geometry (so code holds ground-truth on bounds and free space) and clamps every material to the
   locked palette. Coherence is a gate, not a hope.

3. **Procedural `bpy` blockout is the default geometry source**; local-gen hero props (Hunyuan3D /
   TripoSR via the ComfyUI lease) are deferred behind ADR-0022 §4 autonomous-surface hardening, and
   cloud gen stays rejected (local-first, per research-0012).

4. **Blender EEVEE is the deliverable renderer; Unreal 5.8 is an optional real-time walkthrough
   target — not the output path** until the ADR-0022 §8 render gate flips. They **share the brief,
   not assets**: each builds its own scene from the same theme contract via its own scripting, which
   keeps both coherent *and* means there is no custom Blender→UE asset bridge to build. UE's Sequencer
   earns its place for previewing the move (and live interactivity), but a film-quality frame can't
   come out of it on Linux yet, so the artifact comes from EEVEE.

5. **The path is one dual-purpose spline** — the same curve is both the visible route motif
   (`path.render_as`) and the camera rail — so what you *see* and what the camera *rides* are
   guaranteed to agree. The camera move is **deterministic disposal of model-proposed intent**
   (`camera.move`/`path` are a fixed enum vocabulary, not a free camera script), gated by a
   **bounds/clip validator**: code raycasts at N stations along the spline, nudges a failing station
   off the nearest surface normal, or regenerates the spline with more clearance. No model in that
   loop — pure geometry is what makes the path *tracked* and not *random*.

6. **Lease-gated, fail-open, reversible end to end.** EEVEE renders under the `AdoptScope` lease; the
   output is a **reviewable proposal** (ADR-0019 queue + ADR-0009 Surface-B notification-as-control),
   never an auto-applied desktop change; the **brief + the generated `.blend`** are saved alongside it
   for reproducibility and revert-by-construction; gen failure degrades to a simpler blockout or a
   still (ADR-0003), never wedges, never half-applies.

7. **Light interactivity is a live shader uniform, not a new surface.** Desktop signals (canonically a
   window-drag vector → wind direction/gust) drive the **dream-as-texture** layer — ADR-0009-compliant
   because the baked render carries the *look* and the procedural shader carries the live *motion* (a
   baked loop bakes ONE wind direction, exactly ADR-0009's argument). It rides the existing
   `feed.rs` → `agent.json` → uniforms producer→consumer shape (ADR-0001 — don't reinvent), is
   **low-passed by the consumer-side spring** (the easing lives in the QML consumer and the Rust wind
   sink at ω≈1.5 — `feed.rs` itself is edge-driven and stateless, not the damper) so a drag nudges and eases back below the
   attention-capture threshold, is model-proposes-binding-intent / code-disposes-the-pure-mapping (no
   model in the live loop), fails open to neutral wind, and keeps idle byte-identical. The film artifact
   and the live ambient scene are **two modes of one brief**, not two scenes.

8. **Asset ingestion is off-the-shelf — no custom bridge.** In-engine procedural is **PCG** (default-on,
   Linux-native, driven from `editor_run_python`); model import is **Interchange + glTF/GLB** via
   `unreal.AssetImportTask` (the glTF translator ships compiled for Linux; make glTF/GLB the house
   interchange format). Megascans/Fab are **human-in-the-loop only** (no download API, GUI-bound,
   VRAM-heavy — do not design an automated fetch). The ADR-0022 §8 render gate is **unchanged**: Linux
   import is fine; the Windows/DX12-only limit is film-quality MRQ / Path-Tracer — a *render* gate, not
   an *import* gate, so EEVEE stays the deliverable renderer.

## Consequences

- **Honest record.** This ADR is *Proposed* (ADR-before-code discipline, like ADR-0009/0022); it stays
  Proposed pending the human's disposition of the behavior. ADR-0009 is **not** superseded — its
  reserved 3D slot is fulfilled, not breached.
- **Reachable now is real; the rest is gated, not lazy.** Phase 0 (prompt → brief → `bpy` blockout +
  palette-clamp + path-spline + validator → EEVEE move → lucid/Surface-B) is a genuine end-to-end loop
  on **all-green** verified parts (forge MCP, `AdoptScope`, EEVEE, the lucid seam) — buildable today. It
  trades photoreal gen for procedural blockout and ships the calm/abstract aesthetic ADR-0009/0022
  already chose. The "anything from a prompt" generality — local-gen hero props and UE MRQ film renders
  — is **Phase 2**, behind ADR-0022 §4 hardening and the §8 Windows-only render gate. (The UE
  *walkthrough* target also waits on a UE `Spawn`/process-group lease profile — design-intent, not
  `AdoptScope`, since UE isn't a flatpak scope.)
- **A new producer is owed.** Live interactivity needs a **KWin window-drag script** (the
  kwin-mcp / computer-use lane) that writes a versioned drag vector to a runtime file beside
  `agent.json`. Its privacy posture is fixed up front: **geometry deltas only, never window content** —
  a note owed to the privacy-skeptic and the wayland-computeruse-reviewer. The interactive mode is a
  **parallel track**, reachable as soon as that producer lands, independent of the render phases.
- **The calm constraint is the tripwire.** Wind that snaps to every drag is an attention magnet — the
  thing the ambient vision forbids. The signal is bounded and eased; if the live wind ever reads as an
  attention magnet, it **reverts to neutral** (the ADR-0009-spirit dissent-as-tripwire: a visible onset
  transient rolls the surface back to its resting, signal-free state).
- **Reversible by construction.** Output is a held proposal (ADR-0019); the brief + `.blend` are saved;
  nothing on the desktop moves to produce one. The live binding is a pure-function uniform with an
  allowlisted `signal → target` vocabulary — nothing persisted to desktop state, removable by deleting
  the binding. If a walkthrough ever drives the wallpaper directly, that swap is a desktop-state
  mutation and **must** route the ADR-0005 apply/rollback tx — out of scope here.
- **Schema is a two-consumer contract.** The brief, like `agent.json` (ADR-0009 consequence), is read by
  both Blender and (later) UE; version it before the second consumer lands so a schema change can't
  silently desync the two builds. Tracked as an open question in the spec, not a blocker here.

## Council review (2026-06-18) — verdict + amendments

A full design-council pass (11 lenses →
[scorecard](../design/0023-creative-environment-council-scorecard.md)) scored this **~7.5/10: SHIP the
Phase-0 render pipeline; ITERATE the live wind mode + painterly craft.** The eight decisions above stand;
the review added these binding amendments:

- **Honesty on status.** The Phase-0 render *is* verified end-to-end (the amber-field `move.mp4`
  re-rendered, the bounds/clip validator 24/24 clean, geometry determinism by SHA-256, contract tests
  green). The **live wind loop is NOT yet verified end-to-end** — the shader, an interactive viewer
  (`live.qml`), and the `org.agentos.Wind1` sink are built, but the KWin→D-Bus→sink→uniform round-trip is
  unproven on live Wayland (`interactiveMoveResizeStepped` per-step firing + `callDBus` reachability are
  open). Read it as "render-verified; live producer/sink built-but-unverified."
- **`org.agentos.Wind1` folds into the `lease` daemon — ratified, with a hard guarantee.** The window-drag
  sink (the consumer-side spring + atomic `wind.json` write) is served by the existing `lease` daemon
  (which already owns `org.agentos.Coordinator1`), not a new always-on process. **Binding guarantee:** the
  wind tick runs as a separate task that takes **no arbitration lock and can never delay a preemption
  SIGKILL** (resource-safety's load-bearing condition); it fails open to neutral. This is a behaviour
  change on the safety daemon and is now part of this ADR's decision surface.
- **Neutral-vector contract.** The consumer computes **`windDir = dir·gust`**, so at rest (gust→0) the
  wind vector is exactly (0,0) — preserving the shader's idle-byte-identical invariant against the *real*
  producer (the sink may emit any resting `dir`; the magnitude is `gust`).
- **The bindable-signal allowlist is a versioned contract**, not prose: a brief's `bindings` validate
  against an allowlisted, versioned `signal → target` table (today: `window-drag.vector → wind.direction`,
  `window-drag.speed → wind.gust`) — the same versioning discipline as the brief schema.
- **One observable success/kill metric per mode.** *Render:* the validator repairs-or-declines clipping
  stations with zero model-in-loop **and** the first sample renders under the lease without OOM. *Wind:* a
  measured tripwire — if the eased wind reads as an attention magnet in a moving A/B, it reverts to neutral
  and the mode stays gated. No metric → not shipped.
- **Painterly is not yet delivered (recorded dissent).** The art-director holds the stylization reads as a
  posterize filter, not brushwork; Phase-0 ships the calm/legible procedural look — the painterly artifact
  iterates (scorecard P2) before this is called done on the visual axis.
