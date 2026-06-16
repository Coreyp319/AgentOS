# ADR-0008: Dreaming pivots from real-time 3D to generated video segments

- Status: Superseded by [ADR-0009](0009-dreaming-shader-primary-video-as-texture.md) — design-council review, 2026-06-16
- Date: 2026-06-15
- Relates to: ADR-0001 (substrate, not orchestrator), ADR-0003 (fail-open supervised), ADR-0004 (graphics yield: kill/relaunch), ADR-0005 (apply/rollback)
- Research: [docs/research/0001-local-video-models.md](../research/0001-local-video-models.md)

> **Superseded (2026-06-16).** A full design-council review (the reviewer + rating
> panel, then the design team, reconciled by the mediator — see the
> [scorecard](../research/0002-dreaming-panel-scorecard.md) and the
> [design synthesis](../research/0003-dreaming-design-synthesis.md)) found three
> load-bearing faults, any one sufficient: (1) decision §4's `POST /free` eviction
> lever freed **0 VRAM** when measured live on the box; (2) decision §1 / consequence
> "shader = fallback" **inverts** the proven embodiment grammar; (3) decision §6's
> NSFW-capable, enabled-by-default auto-surfacing **breaches consent**. The *intent*
> (3D as north star, one local backend, a dream as a reversible cached artifact) is
> preserved and re-proposed in
> [ADR-0009](0009-dreaming-shader-primary-video-as-texture.md). This document is kept
> intact as the record of what was decided and why it was walked back.

## Context
"Dreaming" is the ambient layer where the agent's mood is carried by the
wallpaper rather than a chat window. Today it is **real-time 3D** — GLSL shaders
and Bevy scenes reacting frame-by-frame to `agent.json` (`{state, busy, warm,
snag}` written by `agentosd feed`; see the `hills-reactive` spike). The ambition
is fully model-generated 3D dream scenes, but **local models cannot yet produce
strong real-time 3D**. Generated **video** is, in mid-2026, good enough locally.

So: pivot the *output medium* of dreaming from real-time 3D to pre-generated
**video segments**, while keeping 3D as the north star. A six-lane research pass
(2026-06-15) mapped the local open-source video landscape against this box
(RTX 4090, 24 GB). Headlines: the simplest runtime that is *both* a UI and a
programmatic API is **ComfyUI**; the 24 GB-viable, clean-license models are
**Wan 2.2** (Apache-2.0) and **HunyuanVideo 1.5** (Tencent community license);
NSFW capability is a model/LoRA choice, not a license grant (no major license
bans adult content; all ban minors / non-consensual likenesses).

A second requirement landed during research: the same capability must also be
reachable **on demand from KRunner**, not only ambiently. That makes two
consumers of one backend.

## Decision
1. **Pivot dreaming output to generated video loops.** 3D remains the long-term
   goal; this is a medium change, not an abandonment. The wallpaper gains a
   `Video` consumer keyed by `agent.json` state; the shader path stays as the
   reduced-motion / fallback renderer.
2. **One local backend: ComfyUI.** It is the only option that is simultaneously
   a UI, an HTTP API (`/prompt`, `/history`, `/free`), and the home of the
   community LoRA ecosystem the NSFW path needs. This is **not** a second
   orchestrator (reaffirms ADR-0001) — it is a leaf generator that `agentosd`
   drives. A diffusers sidecar remains the future option for tightest
   in-process VRAM control; ComfyUI is the pragmatic now.
3. **Two surfaces, one backend** (`dreaming/`): Surface A = ambient wallpaper
   (image-to-video), Surface B = a Plasma 6 **D-Bus** KRunner runner
   (text-to-video, `video: <prompt>`) — no C++ plugin to compile.
4. **VRAM yield (extends ADR-0004).** Video generation wants most of the 24 GB,
   so it is a **leaseholder the coordinator can evict**: `agentosd` owns the
   ComfyUI lifecycle and calls `POST /free` (unload models) as the release
   lever, the same way it does `ollama stop` / nimbus-flux kill-relaunch. Live
   inference outranks dreaming; dreaming is best-effort and offline/cached.
5. **Models:** **Wan 2.2** (Apache-2.0) is the default and the only one eligible
   for bundling into an installer — fp8 14B for quality (4-step lightx2v LoRAs),
   5B TI2V for fast/low-VRAM. **HunyuanVideo 1.5** is offered for quality but is
   **local-only, never bundled** (Tencent community license excludes EU/UK/KR
   territory + carries a mandatory NOTICE).
6. **SFW + NSFW** is a per-generation model/LoRA choice. Honor the one universal
   red line in every license: exclude content involving minors or
   non-consensual real-person likenesses.

## Consequences
- **Reversible by default (ADR-0005 spirit).** A dream is now a cached artifact
  (an mp4/webm file), not live GPU state — trivially swappable, deletable, and
  the wallpaper can fall back to the shader renderer instantly. Generation never
  mutates system/desktop config; it only writes a file the wallpaper *may* play.
- **Determinism (model proposes, code disposes).** The prompt/model is the
  non-deterministic proposal; the deterministic disposition is "write a file to
  the dream cache." No model output reaches a state-mutating path.
- **New resource-safety surface.** A new high-VRAM lease type joins the
  coordinator; OOM math must treat a generation as evictable mid-run. Default
  posture stays fail-open (ADR-0003): if generation can't get VRAM, dreaming
  degrades to the shader path rather than starving inference.
- **Licensing is now load-bearing.** Bundling is restricted to Apache-2.0
  weights (Wan); Hunyuan and any Civitai LoRAs are user-installed, local-only.
- **Footprint.** ComfyUI + weights live at `~/ComfyUI` (uv venv, ~tens of GB),
  outside the Cargo workspace; only the thin client + KRunner runner live in the
  repo (`dreaming/`).
- **3D is deferred, not dropped** — when local 3D generation matures, it slots in
  behind the same `agent.json` seam and the same eviction lease.
