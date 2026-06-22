# ADR-0040: Lucid — prompt-guided keyframe edit ("edit-then-animate")

- Status: **Proposed — backend + frontend BUILT + unit-tested (35 new tests; full lucid suite green) and the
  Qwen-Image-Edit producer is GPU-VALIDATED on the 4090** (the graph runs end-to-end, produces a visibly-edited
  keyframe — mean |Δ| 12.2/channel vs the source, ~32 s cold / ~20 s warm at 4-step Lightning, peak +13.85 GB
  over the desktop base). Reviewed by **a 3-lens pass (privacy+security+determinism)** AND **a 55-agent deep
  audit** (6 dimensions × 3-skeptic adversarial verification) — both **SHIP-AFTER-FIX, no Critical**; all
  in-scope findings applied (see §4/§5). The live `/api/edit/*` routes are verified against the running
  service (CSRF, gating, token, revert). Still OWED before Accepted: the full live preview→commit→animate web
  round-trip on a NON-private dream (couldn't run it without disturbing Corey's active private dream), and an
  eyes-on judgment of edit quality across a few dreams.
- Date: 2026-06-21 (deep-audit hardening 2026-06-22)
- One model download (~22 GB on-box already): Qwen-Image-Edit-2509 **Q4_K_M GGUF** + the Qwen2.5-VL fp8 text
  encoder + the Qwen-Image VAE + the 4/8-step Lightning LoRAs (all **Apache-2.0**), into `~/ComfyUI/models/`.
- Builds on (does not supersede) [ADR-0014](0014-lucid-interactive-dream-tree.md) (the branching dream tree),
  [ADR-0015](0015-lucid-mvp-lease.md) (the leased linear chain + warm-keep), [ADR-0023](0023-creative-environment-pipeline.md)
  (moment-tag steering + the fuse→dream readback grammar this reuses), [ADR-0033](0033-lucid-quality-two-tier-and-identity-carry.md)
  (identity carry — the edit model's identity preservation is *why* Qwen was chosen). Honors
  [ADR-0003](0003-fail-open-supervised.md) (the edit pass is TOTAL fail-open — a decline never blocks the normal
  beat path), [ADR-0005](0005-apply-rollback-transaction.md) (the in-place "replace" keeps a once-only backup and
  is revertible; "branch" is purely additive), [ADR-0016](0016-lucid-private-ephemeral.md) (private edits seal +
  burn, no residue), [ADR-0017](0017-lucid-seed-likeness-guard.md) (a reference image passes the SAME B2
  real-person likeness/consent gate as a seed). Mirrors [ADR-0032](0032-lucid-click-to-segment-spatial-markup.md)'s
  warm-lease producer contract exactly.

## Context — the gap

Mid-dream, a viewer can steer the **next beat** only with *text* — pick or refine a beat, tag a moment
(`/api/note` + a SAM2 region), and edit the composed "Shot Card" prompt before committing. Every clip is seeded
from the auto-extracted **last frame** of the prior clip; there is no way to bring **your own image** into the
loop after the opening, and no way to set the **starting pose** of the action precisely. The ask: *"seed an edit
via image and prompt, to allow fine-tuned direction of the action."*

The single most effective lever on i2v motion is the **starting keyframe**. So the feature is: point at a frame,
describe the action ("raise the lantern, turn toward the glow") and optionally drop a reference image; a
prompt-driven image-edit model produces a **new keyframe**; the existing i2v path animates **from it**.

## Decision

**Edit the keyframe with a prompt, then animate from it.** Two user-chosen placements: **branch** a new beat from
the edited keyframe, or **replace** the current shot in place (revertible).

### 1. The keyframe producer (`lucid_engine.edit_frame`)

An edit is **not** a new video workflow — `step()` already seeds the clip from a swappable `anchor` frame. So the
only net-new generation is a **keyframe producer** that mirrors `segment_at()`'s proven warm-lease contract:

- Runs as **one ComfyUI prompt inside the already-leased warm batch token** — **no second lease**, so it can't
  self-preempt the dream.
- **Queue-gated** (`_comfy_busy` → never evict a running render) → **VRAM reclaim** (`cc.free_vram`, since the edit
  model can't co-reside with a warm i2v: ~12 GB + ~17 GB > 24 GB) → **free-VRAM headroom gate**
  (`EDIT_PEAK_MIB`, measured 14.5 GB) → submit. Peak VRAM stays **max(edit, i2v)** because the two run as
  *sequential* prompts (ComfyUI evicts/reloads between them; the fp8 TE never co-resides with the unet).
- **TOTAL fail-open**: any miss (disabled, empty instruction, cold/contended ComfyUI, corrupt output, exception)
  returns `None`, and the caller animates the **un-edited** frame — an ordinary beat.
- **Model: Qwen-Image-Edit-2509** (Apache-2.0; native ComfyUI `TextEncodeQwenImageEditPlus` — the *Plus* node
  takes the optional reference images directly; best-in-class identity preservation, serving ADR-0033's
  "changing faces" concern). Graph = the official ComfyUI **"Image Edit (Qwen 2509)"** template with the loader
  swapped to GGUF: `UnetLoaderGGUF → LoraLoaderModelOnly(Lightning-4step) → ModelSamplingAuraFlow(shift 3) →
  CFGNorm → KSampler(4 steps, cfg 1, euler/simple, denoise 1)`, source `VAEEncode`d as the latent. Lightning is
  default (interactive); `LUCID_EDIT_LIGHTNING_LORA=""` selects the full 20-step/cfg-4 lane. All knobs are env
  (`LUCID_EDIT_{ENABLED,MODEL,TE,VAE,LIGHTNING_LORA,SHIFT,PEAK_MIB}`), matching the `SEG_*` convention.

### 2. Two-step preview → commit (the readback grammar)

Mirrors `fuse → dream`: the cheap edit (~tens of seconds) **shows the keyframe for approval BEFORE** the
minutes-long i2v, so a bad pose never wastes a render.

- `POST /api/edit/preview {node, prompt, image_b64?, placement, consent?}` — red-line-gate the instruction; B2
  real-person gate + ingest the optional reference image; run `edit_frame` under the warm lease; return the
  keyframe **inline** as a no-store data-URL + a single-use commit `token`. Synchronous + serialized (`_EDIT_SEM`)
  + holds the TURN as `dreaming` for its duration so the idle reaper can't release mid-edit and a concurrent beat
  is refused.
- `POST /api/edit/commit {token, length?}` — animate the approved keyframe on a worker (mirrors `_run_turn`'s
  lease + epoch discipline). `branch` → `step(anchor_override=…)` (the edit IS the steering, so the parent's
  moment-notes are skipped); `replace` → `replace_beat(...)`.
- `POST /api/edit/revert {node}` — restore the once-only backup (`replace_beat` keeps `node['prev']`; a 2nd edit
  keeps the FIRST original, so revert always returns to the source shot).

### 3. Determinism, safety, privacy

- **Model proposes, code disposes**: the model only produces a *keyframe image* — a deterministic, validated
  (`_save_validated_image`), reversible artifact; it never mutates chain state directly. The commit goes through
  the same red-line + lease + epoch gates as any beat.
- **The new image-injection surface is gated**: the instruction passes the red-line gate (twice — route + engine);
  the optional reference image passes the **same B2 likeness/consent guard as a seed** (ADR-0017), with the
  consent re-try flow. Private dreams seal the edited keyframe + reference into the private subdir (burned on
  logout) and the shared-output scratch is scrubbed on **every** path (success, reject, OR an exception that
  left a partial — a `finally` glob, so no private real-person keyframe outlives the call in shared output).

### 4. Review hardening (3-lens pass, 2026-06-21)

Privacy + security + determinism reviewers (all **SHIP-AFTER-FIX, no Critical**) drove these fixes, applied:
the scratch scrub moved into a `finally` (the privacy High — exception-path residue); `cc.free_vram` given a
socket timeout (the determinism High — a wedged `/free` must not hang the synchronous edit holding the lock +
TURN); `replace_beat` re-loads the chain after the render (the lost-update Med — mirrors `rerender_hero`); the
edit fenced against an in-flight SAM2 segment via the shared serializer (the security High — cross-lane
`free_vram` eviction); plus atomic keyframe write, an `EDIT_PENDING` cap + scratch unlink, and an `out_frame`
traversal guard.

### 5. Deep-audit hardening (55-agent adversarial audit, 2026-06-22)

A second, broader audit (6 dimensions — graph correctness, frontend/a11y, concurrency, reversibility, fail-open
regression, completeness — each finding verified by a 3-skeptic majority vote) returned **SHIP-AFTER-FIX, no
Critical**, 11 confirmed findings. **Applied:**
- **R1 (High)** `/api/edit/revert` had no in-flight guard → a concurrent `replace_beat` commit could silently
  un-revert. Revert now refuses while `TURN=='dreaming'` and the frontend Revert button is gated on the server
  flag (not client-local `busy`).
- **C1 (Med)** the SAM2-segment fence was scoped to the whole preview incl. the ~190 s cold spawn, starving
  every `/api/segment` tap. Now scoped to just the `edit_frame` GPU op (free_vram→submit).
- **F1 (Med)** the commit mutation closed the panel on *any* outcome (`onSettled`), silently losing the approved
  keyframe on a declined commit. Now keeps the keyframe + surfaces the reason unless a turn actually started.
- **G1** the keyframe is cover-fit to the i2v input size (720×1280) in `_save_validated_image`, so *what you
  approve is exactly what gets animated* (was: a Kontext-bucket aspect that `WanImageToVideo` then edge-cropped).
- **G2 (perf)** at cfg=1.0 (Lightning) the unused negative encode is elided (reuse the positive node) — a real
  latency cut on the synchronous preview.
- **CP1** `edit_enabled` now also checks the GGUF is on disk (`E.edit_available()`), so a flag-on box without the
  weights hides the affordance instead of failing late. **CP2** `edit_frame` validates the source frame exists
  before the VRAM swap. **F2/F3** revert announces its outcome via `aria-live`; the placement chips use
  `role="group"`+`aria-pressed` (operable, vs a non-navigable `radiogroup`).

**Deferred (out of ADR-0040 scope — pre-existing shared infra, tracked as follow-ups):** **R2** a per-session
chain write-lock spanning `load→mutate→save` + a per-write-unique temp name (a lost-update window across *all*
chain ops, not just edit; R1 closes the edit-specific race); **C2** make the preview async (202 + poll) instead
of holding the sync handler across a cold spawn; plus `purge_persistent` enumerating `prev` clips for
complete-by-construction edited-dream deletion, and a `/api/edit/cancel` to eagerly unlink an abandoned preview
keyframe. **Confirmed-correct (no defect):** the sampler/shift/CFGNorm chain mirrors the official template; a
private `out_frame` passes the traversal guard; the idle reaper correctly skips `dreaming` mid-edit.

**Reference-image semantics (clarification):** the optional reference is an *identity/look* cue woven into the
edit (Qwen `TextEncodeQwenImageEditPlus`, up to 2 refs), **not** a pose/composition seed — the instruction text
drives the pose; the source frame is image1, refs are image2/3.

## Consequences

- **New**: an image enters the steering loop for the first time after the opening; precise control of the
  action's *starting pose*; an in-place "fix this shot" with undo (the dream's first reversible mutation).
- **Cost**: a Qwen↔i2v model swap per edit (~one reload), acceptable for an explicit, user-initiated action;
  ~22 GB of weights on disk.
- **Bounded**: replacing a shot does NOT retroactively re-render its children (they keep their stored anchors);
  documented, and the child can be edited too. Edit availability is surfaced via `/api/state.edit_enabled` and is
  a hard kill-switch (`LUCID_EDIT_ENABLED=0`).
- **Owed**: the live web preview→commit→animate round-trip on a NON-private dream, and an eyes-on quality read;
  the §5-deferred follow-ups (R2 chain write-lock, C2 async preview); per-edit latency could later be cut by
  keeping a smaller GGUF resident or batching the swap.
