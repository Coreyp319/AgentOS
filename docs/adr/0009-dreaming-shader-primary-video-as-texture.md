# ADR-0009: Dreaming — procedural shader is primary; video is texture-input + on-demand, not the medium

- Status: Proposed
- Superseded in part by: [ADR-0029](0029-ue-wallpaper-primary-shader-fallback-floor.md) — **§1**'s
  primary/fallback ordering is inverted for the *ambient wallpaper surface only* (live UE 5.8 is
  primary; the procedural shader becomes the mandatory fallback floor). §2–§4 and the
  idle-byte-identical contract **stand** and continue to constrain ADR-0029.
- Date: 2026-06-16
- Supersedes: [ADR-0008](0008-dreaming-via-local-video-gen.md) decisions §1, §4, §6 (and the
  "shader = reduced-motion / fallback renderer" framing). Keeps ADR-0008's intent: 3D as the
  north star, ONE local backend (ComfyUI as a leaf generator, not a second orchestrator —
  ADR-0001), and a dream as a reversible cached artifact.
- Relates to: ADR-0001 (substrate, not orchestrator), ADR-0003 (fail-open supervised),
  ADR-0004 (graphics yield: kill/relaunch), ADR-0005 (apply/rollback)
- Review: [scorecard](../research/0002-dreaming-panel-scorecard.md),
  [design synthesis](../research/0003-dreaming-design-synthesis.md)
- Evidence (spikes): [`spikes/video-wallpaper`](../../spikes/video-wallpaper/README.md),
  [`spikes/dream-as-texture`](../../spikes/dream-as-texture/README.md),
  [`apps/dreaming`](../../apps/dreaming/README.md)

## Context

ADR-0008 accepted "pivot dreaming's output medium from real-time 3D to pre-generated **video**,"
and demoted the proven GLSL shader grammar (`docs/vision.md`, the `hills-reactive` spike, the
`feed.rs` producer) to "the reduced-motion / fallback renderer." A full design-council review on
2026-06-16 — the reviewer + rating panel, then the ten-member design team, reconciled by the
mediator — found that pivot wrong on three independent grounds, and reframed the medium question
with two spikes built during the review.

**What the review established (ground truth):**

1. **The eviction lever is empirically false.** `rater-feasibility` measured `POST /free
   {unload_models, free_memory}` live on this box: VRAM 21540 → 21571 MiB (no drop), ComfyUI
   still holding ~17 GB. ComfyUI's `/free` cannot reclaim VRAM mid-generation and did not even at
   idle. ADR-0008 §4 ("evictable mid-run, the same way it does `ollama stop`") does not hold.
2. **Demoting the shader inverts the embodiment vision.** The agent signal is **continuous** —
   `feed.rs:88-97` emits `busy`/`warm`/`snag` as eased floats (ramped 0.7→1.0, …), low-passed
   through a 2–20 s spring so "the same scene runs harder." A pre-baked loop bakes ONE motion-rate
   into its pixels; expressing the continuous signal needs either a clip-per-state cliff (the very
   thing the architecture removed) or a crossfade — and a crossfade **is** the discontinuity, not
   its cure. Vision science agrees: smooth procedural motion sits below the attention-capture
   threshold; a loop's periodic seam is exactly the onset transient that grabs the eye. And a loop
   has no "zero," so it cannot honor idle = byte-identical-to-the-unmodified-wallpaper.
3. **The default posture breaches consent.** The KRunner runner shipped `EnabledByDefault=true`,
   ran generation on the Enter key, `xdg-open`'d an NSFW-capable clip with no preview / consent /
   cancel / undo, and dumped raw tracebacks into notifications.

**The reframe (two spikes):** `spikes/dream-as-texture` showed generated video and frame-by-frame
float responsiveness are *not* in tension if the video is the **substrate the procedural layer
warps** (`sampler2D dreamTex`), not the **output** a state picks. `spikes/video-wallpaper` measured
the renderer: HW video decode is broken on this NVIDIA/VA-API stack but **irrelevant** — software
decode runs 4K @ ~400 fps (NVDEC ~397 MiB/stream when used), and the `VideoOutput → ShaderEffect`
grade seam holds the idle-byte-identical contract. The one unproven bit is the seamless **loop
seam** (a ~10-minute on-session eyeball).

## Decision

1. **The procedural shader is the permanent primary renderer of agent state.** Reverse ADR-0008's
   "shader = fallback." The shader carries the continuous `{state, busy, warm, snag}` signal,
   always. Video never carries agent state on the ambient surface, and idle stays byte-identical to
   the unmodified wallpaper (all agent terms → 0). The signal vocabulary is exactly what
   `derive_feed` emits today — states `idle`/`working`/`needs_you`/`snag` (0/1/2/4) and the eased
   `busy`/`warm`/`snag` floats. **No surface keys on the declared-but-unemitted `acting` state**
   (3, `feed.rs`); a future `acting` look waits on the computer-use backend producing it, behind the
   same seam.

2. **Two surfaces, two distinct media** (one backend, `apps/dreaming/comfy_client.py`):
   - **Surface A — ambient "dreaming."** Video, *if present*, enters as a **texture input** the
     procedural shader warps (dream-as-texture); the floats stay continuous uniforms. Renderer is
     **software-decode + `ShaderEffect` grade** (per `spikes/video-wallpaper`). **SFW-only**, with
     NSFW **architecturally** excluded from this path. **Muted by contract** (no audio track ever
     reaches a sink). A **system-owned post-grade** is mandatory before any clip composites, so
     `warm`/`snag` are the *same* parametric grade on both the shader and the video path — never a
     prompt word. At idle the layer resolves to the shader (or a still), never a playing loop.
   - **Surface B — on-demand "generate."** The user explicitly asks for a clip
     (`apps/dreaming/krunner_video_runner.py`). Completion is **notification-as-control**
     (Preview / Set-as-wallpaper / Discard) — **no auto-`xdg-open`**. Consent is a first-run gate
     plus a KRunner *action*, **opt-in** (`EnabledByDefault=false`). NSFW is **imperative-only**,
     behind a **fail-closed** red-line guard (no minors, no non-consensual real-person likeness).

3. **Eviction redesigned** (supersedes ADR-0008 §4). `agentosd` **owns the ComfyUI process**
   (spawns it, holds its PID), does **admission control** — *predict before load*: refuse a
   generation when `free_vram < estimate` — and uses **`SIGKILL` of the owned PID** as the
   trustworthy hard backstop. `POST /free` is demoted to a best-effort hint (measured: freed 0 VRAM).
   Video-gen and live inference are **mutually exclusive on VRAM** (never concurrent; ~2.5 GB free
   with a model loaded). Live inference outranks dreaming; dreaming is best-effort and degrades to
   the pure shader (fail-open, ADR-0003).

4. **Naming.** "dreaming" names the **ambient resting-state mood** only. The on-demand generator is
   **"generate" / "make,"** never the agent's ambient voice. Failure copy is plain and recoverable
   ("Couldn't finish that video · Nothing changed on your desktop · Try again"); tracebacks go to a
   log, not a notification body.

5. **Gating & reversibility.** All of the above is **`[SUBSTRATE-BLOCKED]`** on the still-unbuilt
   VRAM coordinator (decision 3) and the ADR-0005 apply/rollback tx. Dream **generation** (writing
   an mp4 to the cache) stays *outside* the tx — correct per ADR-0008. But the **active-dream
   wallpaper swap** is a desktop-state mutation and **must** route through the ADR-0005 tx (atomic,
   diffable, revertible) — "fall back to the shader" is a degraded render, not a rollback. **Surface
   B ships before Surface A.** Surface A is a **gated experiment** behind the one on-session
   loop-seam run.

## Consequences

- **Honest record.** ADR-0008 is marked *Superseded* and kept intact; this ADR is *Proposed*
  (ADR-before-code discipline) pending the human's disposition of the behavior change.
- **The two live caps are designed-closed, not yet coded.** The auto-open consent breach is closed
  by decision 2 (notification-as-control + opt-in); the `/free` eviction failure by decision 3
  (own-PID + admission-control + SIGKILL). Both remain open until the coordinator and the Surface-B
  UX land.
- **Backend is real; the layer around it is not.** `apps/dreaming` validated all five generation
  paths (Wan 5B SFW/NSFW, Wan 14B SFW, Hunyuan SFW/NSFW) on 2026-06-16 — generation works. What is
  missing is coordination (decision 3), consent/UX (decision 2), the post-grade + NSFW wall
  (decision 2), and the dream-as-texture wallpaper consumer (decision 2 / `spikes/dream-as-texture`).
- **Cost accepted.** Video never carries agent state on the ambient surface; one short cached clip
  held as a texture is cheaper than a per-state loop library but is *not* free and is *not* yet
  evictable until decision 3 ships.
- **Recorded dissent.** `art-director` + `brand-identity-designer` would confine video to Surface B
  only. Their position is the **tripwire**: if the loop-seam run shows any visible onset transient,
  Surface A reverts to pure procedural.
- **Routing owed before Surface B ships:** `channels-integration-reviewer` (the KRunner D-Bus
  channel — authz on `Run()`, which is currently unauthenticated) and `ai-product-reviewer` (the
  "generate" verb / scope).
- **`agent.json` becomes a two-consumer contract** (feed → shader *and* feed → dream-as-texture
  grade). It must be **versioned** before the second consumer lands, so a schema change can't
  silently desync the two renderers — owed to `reversibility-tx-reviewer` (a short follow-up ADR if
  the schema grows).
- **3D is still deferred, not dropped** — it slots in behind the same `agent.json` seam and the same
  coordinator lease when local 3D generation matures.
