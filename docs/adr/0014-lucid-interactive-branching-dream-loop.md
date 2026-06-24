# ADR-0014: Lucid — interactive, branching dream loop (Surface B evolution)

- Status: Proposed
- Date: 2026-06-16
- Extends: [ADR-0009](0009-dreaming-shader-primary-video-as-texture.md) **Surface B** ("on-demand
  generate"). Does not supersede it; inherits every Surface-B constraint (consent gate, opt-in,
  notification-as-control, no auto-`xdg-open`, NSFW imperative-only behind a fail-closed red-line,
  plain failure copy, naming discipline).
- Relates to: ADR-0001 (substrate **not** orchestrator — reuse Hermes/Ollama for the narrative LLM,
  do **not** build a story engine), ADR-0005 (apply/rollback — the tree is reversible; "set as
  wallpaper" routes through the tx), ADR-0006/0010/0013 (VRAM coordinator: lease lifecycle + IPC
  trust — the loop's heartbeat), ADR-0012 (keyhole: a dream-in-flight is a visible job).
- Evidence (spike): [`apps/dreaming/lucid`](../../apps/dreaming/lucid/README.md) — the thin
  "click → the story continues" slice (throwaway, runs generation directly, **no lease yet**).
- Reviewed: [`docs/research/0008-lucid-review-scorecard.md`](../research/0008-lucid-review-scorecard.md)
  — 8-dimension design council (2026-06-16). **Verdict: HOLD as ship design / ITERATE as spike**;
  blockers in resource-safety (co-residency OOM), responsible-ai (unguarded real-person seed → i2v),
  reversibility (non-atomic tree). MVP steer: build a *linear* chain through the coordinator lease
  first; defer branching, the QML panel, §6 grounding, and "set as wallpaper" behind that gate.

## Context

ADR-0009 framed Surface B as a one-shot: type `video: <prompt>`, get one disconnected clip. In use
that is a slot machine — the I2V anatomy win (REF §NSFW: "image-to-video from a clean first frame is
the single biggest anatomy win") is thrown away every clip, and the user has no authorship beyond
the opening prompt.

This ADR reframes Surface B as an **interactive, branching loop**: the user steers a chain of clips
by choosing what happens next. Three facts make it the right shape for *this* substrate, not a
generic feature:

1. **Continuity is free and is the headline.** "Picking up where the last one left off" = the **last
   frame of clip N becomes the I2V first frame of clip N+1**. We proved Remix-v3 I2V works on this
   box on 2026-06-16 (720×1280/33f, full-load, ~4–5 min with contention). Chaining is just
   ffmpeg-extract + re-parameterize the same workflow.
2. **The loop *is* the VRAM-coordinator dance.** Each turn alternates a small, fast **inference**
   step (propose/expand beats) with a large, slow **video** step. ADR-0009 §3 makes these
   **mutually exclusive on VRAM**; ADR-0006/0010/0013 is exactly the arbiter. The contention we hit
   by hand (Ollama crept back, VRAM tightened) becomes the engine's heartbeat, not a bug.
3. **Reversibility is native.** Every clip is a cached file, so the story is a **tree**: branch any
   node, scrub back, reroll. This is ADR-0005's spirit without a tx — generation writes cache, never
   desktop state.

## Decision

1. **Lucid is an interactive branching extension of Surface B**, not a new surface and not a new
   orchestrator. The narrative LLM is **Hermes/Ollama** (ADR-0001: reuse, don't reinvent). A "story
   engine" is a prompt contract over the existing model, not new infrastructure.

2. **Steering = LLM-proposed beats + free-text override.** Each turn the LLM returns **3–4 short
   "next beat" labels**; the user clicks one *or* types their own. **Model proposes, code disposes**
   (determinism-safety): the LLM returns a **validated structured object** (beats + the expanded
   I2V motion prompt); code sanitizes it (length caps, red-line filter, prompt-injection strip)
   *before* it is displayed or allowed to parameterize a workflow. A malformed/over-budget/blocked
   response degrades to "type your own," never to an unchecked prompt reaching the GPU.

3. **The GPU dance honors ADR-0009 §3 (never co-resident).** Beat-gen runs as **inference that
   evicts before video** — a small/CPU model, or Ollama with `keep_alive:0` so the model unloads the
   instant beats are produced. The **video beat** is then admitted through the coordinator as an
   **owned, evictable job** (`Spawn`, ADR-0010/0013). **Live interactive inference still outranks
   the dream** (ADR-0009 §3): if the user is actively chatting with Hermes, the dream pauses (its
   lease is preemptible) and the surface fails open to the shader — the loop resumes when the GPU
   frees. No concurrency, ever.

4. **Structure = a persisted branching tree.** Each node = `{clip, caption, beat-label, prompt,
   seed, parent}`, stored under the dream cache (`$XDG_DATA_HOME/agentos/dreams/<session>/` =
   clip files + `tree.json`). Branch = new node off any parent; reroll = new seed off the same
   parent; scrub = navigate. **Generation stays outside the ADR-0005 tx** (per ADR-0008/0009); only
   **"set this clip as wallpaper"** is a desktop mutation and **must** route through the tx (atomic,
   diffable, revertible). A storage-cleanup/quota policy is owed before ship.

5. **Two surfaces, both Surface-B-compliant.** A **panel** (QML; playing clip + beat buttons +
   branch rail + type-your-own) for active authoring, and **notification-as-control** (swaync; the
   next-beat buttons as notification actions) for hands-off "continue while I work." **KRunner is
   ignition only** (`dream:`/`make:` → opens the panel on the first beat); it cannot host a playing
   clip + buttons. Both inherit ADR-0009 Surface B: **opt-in** (`EnabledByDefault=false`), **consent
   gate**, **preview before any apply**, **no auto-`xdg-open`**, plain recoverable failure copy.

6. **Frame grounding (phase 2).** Caption the last frame with **Qwen2.5-VL** (already on disk) and
   feed "what is on screen now" to the beat LLM, so proposed beats are *visually achievable* from the
   current frame instead of narrative non-sequiturs. The thin slice ships without it; it is the
   coherence upgrade, not the MVP.

7. **Consent & the red-line, now spanning two models.** Default **SFW**. Adult tone is an explicit
   per-session opt-in (ADR-0009: imperative-only, fail-closed). Because an LLM is now in the loop,
   the red-line (**no minors, no non-consensual real-person likeness** — SKILL hard constraints) is
   a guard on **both** the beat-gen prompt contract (the model must not *propose* a violating beat;
   output is filtered) **and** the video prompt. Fail-closed: if the guard is unsure, the beat is
   dropped.

8. **Naming is owed, not decided.** ADR-0009 §4 reserves "dreaming" for the **ambient** mood only;
   the on-demand generator is "generate/make." "**Lucid**" is the internal codename for this loop;
   the user-facing verb must not blur the ambient voice. Final naming → `brand-identity` +
   `content-voice`, before ship.

## Consequences

- **A new model-proposes surface enters the safety-critical path.** The beat/prompt contract is
  owed to `determinism-safety-reviewer` + `ai-generation-reviewer` (structured-output schema,
  validation, fallback, injection hardening) before the loop is anything but a spike.
- **The red-line guard now has two enforcement points** (LLM beat-gen + video prompt) → owed to
  `responsible-ai-privacy-skeptic` + `security-reviewer`. The LLM must never be the *only* gate.
- **`[SUBSTRATE-BLOCKED]`.** The shipped loop is gated on the coordinator (ADR-0006/0010/0013) for
  the GPU dance and on the ADR-0005 tx for the wallpaper apply. The `apps/dreaming/lucid` slice
  is **throwaway evidence** that runs generation directly without a lease — explicitly outside the
  ship gate, to prove the interaction is worth building.
- **Inherited debts from ADR-0009 still open**: KRunner `Run()` is unauthenticated
  (`channels-integration-reviewer`); the "generate" verb / scope, and now "is branching the right
  MVP or feature-creep on Surface B?" (`ai-product-reviewer`).
- **New persisted artifact** (the dream tree) → storage growth, cleanup, and "delete my dreams"
  affordance owed (`reversibility-tx-reviewer` for the wallpaper-apply path; privacy for retention).
- **Latency is accepted.** Minutes per beat; this is a slow, contemplative loop — on-brand for
  dreaming, but the waiting must be *designed* (choices "being considered," clip "developing"), not
  a spinner. The keyhole (ADR-0012) is the honest progress surface.
- **No tiny narrative model is installed** (smallest Ollama model on this box is 9.6 GB). Either add
  a small instruct model for beat-gen or accept the evict/reload swap each turn (ADR-0009 §3). The
  spike accepts the swap (`keep_alive:0`); the shipped loop should add the small model.
