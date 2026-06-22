# ADR-0033: Lucid — render quality (Wan two-tier draft/hero + persistent identity carry)

- Status: **Proposed — Python backbone + frontend BUILT + unit-tested (13 tests, full lucid suite green
  2026-06-21). The SHARPNESS half is GPU-verified on the 4090: cheap levers (steps, fp8, native-res) measured
  inert/infeasible, the RealESRGAN 2× upscale measured 8× the frame detail (7→56) and visually confirmed — the
  hero lane is now 20-step + upscale.** Still OWED before Accepted: the IDENTITY half (does the subject anchor +
  deterministic seed reduce face drift) needs a multi-beat dream A/B — that's a per-dream, eyes-on judgment.
  One new ~67 MB download (`RealESRGAN_x2plus.pth`); everything else uses on-disk weights/graphs.
- Date: 2026-06-21
- Builds on (does not supersede) [ADR-0014](0014-lucid-interactive-dream-tree.md) (the branching dream loop),
  [ADR-0015](0015-lucid-mvp-lease.md) (the leased linear chain + warm-keep), and reuses the
  [ADR-0009](0009-dreaming-shader-primary-video-as-texture.md) "a dream is a cached file" stance. Honors
  [ADR-0003](0003-fail-open-supervised.md) (every new path fail-open) and [ADR-0005](0005-apply-rollback-transaction.md)
  spirit (the hero clip is an **additive** artifact — the draft is kept, the chain's `out_frame` anchors are
  untouched, nothing is mutated).
- Relates to: the `local-video-gen` skill REFERENCE (NSFW realism rules #2/#4/#6, the §Long-scenes drift
  note), and the research memory `lucid-comfyui-flow-upgrades`.

## Context — the complaint

The shipped dream output drew two specific quality complaints: **"the face changes between beats"** and a
**"low-res feel."** Both trace to the shipped default render lane (`ENGINE=wan`, the 4+4 "lightning" config),
diagnosed against the live code:

**Identity drift ("changing faces").** A dream is independent i2v clips stitched end to end; each clip's
*only* continuity signal is the previous clip's last frame fed as the i2v seed image. Three compounding
sources of drift, none guarded:
1. **Random seed per beat** — `run_beat` did `seed = random.randint(...)` every clip (and the web path
   *discarded* the returned seed: `clip, _seed = …`, node persisted `"seed": None`). No reproducibility at all.
2. **No identity in the render prompt** — the per-beat prompt is motion-only ("the light turns gold"); the
   subject is never re-described, so each clip re-invents the face/clothing off the single pixel anchor. The
   `premise` field biased beat *proposals* and grounding but never reached the renderer.
3. **No latent continuity** between clips (Wan has no tail-chaining; that lives only on the LTX path, deferred).

**Soft fidelity ("low-res feel").** Not pixel count — the lane already renders 720×1280. The 4-step lightning
config is, by the playbook's own rule #6, a **draft** tier (soft detail, worst on faces/hands), with no
detail/upscale finishing pass, in short 33-frame @16fps loops.

## Decision

Stay on **Wan 2.2** (keep the NSFW anatomy ecosystem — Instareal + NSFW-22 low-noise LoRAs — that LTX/10Eros
doesn't match), and address both complaints with two cheap, engine-honest mechanisms plus a two-tier render:

### 1. Two-tier draft → hero (the "low-res feel" fix)
A new `quality` axis on the Wan render path selects the **workflow lane**:
- **draft** = the existing 4+4 lightning graph (`lucid-nolight-nsfw-i2v-4x4.api.json`, has the lightx2v LoRA),
  ~4.5 min/beat. What the interactive browse loop renders.
- **hero** = `lucid-nolight-nsfw-i2v-hero-up.api.json` = the non-distilled **20-step** graph (no lightx2v, real
  CFG 3.5 — the anatomy/coherence base) **plus a RealESRGAN 2× detail-upscale** spliced between `VAEDecode` and
  `VHS_VideoCombine` (`UpscaleModelLoader` → `ImageUpscaleWithModel`, `RealESRGAN_x2plus.pth`), 720×1280 →
  1440×2560. Rendered **on demand** for a keeper via `rerender_hero` / `POST /api/hero`, reusing the beat's
  stored **seed + prompt + anchor** so it's the SAME shot at higher fidelity — not a re-roll. Stored as
  `node["hero_clip"]` (draft kept); `lucid_stitch` + `/api/clip` play hero when present.

  **The upscale is the load-bearing fix, established empirically on the 4090 (2026-06-21), not assumed.**
  The first cut of this ADR assumed "fidelity is steps, not pixels" — measurement **falsified** that: on the same
  seed image/prompt/seed, frame sharpness (variance-of-Laplacian) was **identical at 7** across the 8-step draft,
  the 20-step GGUF-Q6K hero, AND a 20-step **fp8** hero (precision made no difference either); a higher **native**
  resolution (960×1696) VRAM-**thrashed and timed out** on the live desktop. Only the post-gen detail upscaler
  moved it — **7 → 56** (8×, fair-compared at a common 720p downscale), visually a clean detail lift (hair, skin,
  fabric, background) with no over-sharpen halos. So hero res STAYS at draft res (`LUCID_HERO_W/H` default
  720×1280 — bumping native res thrashes); the **upscaler** provides the pixels. Cost: ~402 s for a 17f hero
  (≈10–12 min for a full 33f beat) — fine for an on-demand "Finalize in HD," wrong for the browse loop (→ draft).
  New runtime dependency: `~/ComfyUI/models/upscale_models/RealESRGAN_x2plus.pth` (~67 MB, GitHub release, no auth).

### 2. Deterministic seed (prerequisite + a small steadiness win)
`start()` mints a per-dream **base seed**; each beat derives a deterministic `seed = base + node_id`
(`_beat_seed`, with a stable `zlib.crc32(session)` fallback for legacy chains), persisted on the node. This is
what makes a hero re-render reproduce the exact draft shot, ends the per-beat random lottery, and gives the
whole dream one noise family.

### 3. Persistent subject anchor (the "changing faces" fix)
A new `ground_subject` VLM pass captures ONE compact identity descriptor from the **opening** frame
(appearance only — not action/mood/background), once per dream, cached on the chain (`_subject_for`). It is
quietly prefixed onto the **render** prompt only (`_with_subject`) — **never** the beat menu, which stays
motion/idea-led ([[lucid-beatgen-prompt-redesign]]). The combined string passes the same red-line gate as any
prompt. Kill-switch `LUCID_SUBJECT_ANCHOR=0`.

## Non-negotiables honored
- **Model proposes, code disposes.** The subject descriptor and the rating are model output; both are
  red-line-gated and the seed/quality/anchor are deterministic code. The two-tier is a deterministic
  workflow-file choice.
- **Fail-open (ADR-0003).** No subject (kill-switch / no frame / model down / red-line refusal) → the render
  is motion-only, exactly as before. A failed hero render leaves the draft + chain untouched. Legacy chains
  (no base seed / no stored anchor) re-derive deterministically.
- **Additive / reversible (ADR-0005 spirit).** `hero_clip` is a new field beside `clip`; `out_frame` anchors
  are NOT re-derived from the hero, so downstream continuity is unchanged. Draft is never discarded.
- **No reinvention.** Both lanes reuse the existing committed Wan workflows + the same `_set_widgets_api`; the
  hero adds only ComfyUI's own `UpscaleModelLoader`/`ImageUpscaleWithModel` core nodes + one small standard
  weight (`RealESRGAN_x2plus.pth`) — glue, not a new pipeline.
- **Warm-keep + lease discipline (ADR-0015).** `/api/hero` runs on the same one-beat-in-flight TURN lock and
  warm batch lease as `/api/dream`, with the same epoch/`is_current` supersede guard — a finalize and a draft
  beat can't run at once and a mid-render start/delete/burn discards the result.

## Consequences
- The browse loop stays fast (draft); the keeper gets HD on demand (hero, ~10–12 min/33f beat) — the chosen posture.
- One extra VLM pass on the first rendered beat of a dream (subject capture), cached thereafter.
- The chain schema gains optional `seed`(base, on the chain) + per-node `seed`/`anchor`/`quality`/`hero_clip`
  + `subject` — all optional with deterministic fallbacks, so old saved dreams load unchanged.
- New runtime dependency `RealESRGAN_x2plus.pth` in `~/ComfyUI/models/upscale_models/` (a fresh install must
  fetch it — the hero render 400s if absent; fail-open: a missing upscaler should degrade hero → 20-step, not
  error — a follow-up guard).

## Status / what's owed
1. ✅ **Sharpness GPU-verified (2026-06-21)**: steps + fp8 + native-res ruled out by measurement; RealESRGAN 2×
   upscale lifts frame detail 8× (Laplacian 7→56) and is visually clean. Hero lane = 20-step + upscale.
2. ✅ **Frontend built**: "Finalize in HD" affordance + HD badge + hero-clip playback in `Chain.tsx`/`api.ts`
   (`useHero` → `POST /api/hero`); `.tag-hd` pill in `theme.css`; tsc + vite clean.
3. **Owed — the IDENTITY A/B**: a multi-beat dream with the subject anchor on vs off (`LUCID_SUBJECT_ANCHOR=0`),
   to confirm the subject prefix + deterministic seed steady the face across cuts without over-fixating / fighting
   a legitimate `change` beat. Per-dream, eyes-on (the agent can't judge identity-drift across a chain). Tune
   `SYS_SUBJECT` phrasing from what's seen.
4. **Owed — robustness**: a fail-open guard so a missing `RealESRGAN_x2plus.pth` degrades hero → plain 20-step
   instead of erroring; VRAM watch on the upscale of long (33–49f) beats on a contended desktop; wire the model
   fetch into the install/components path. Optional later: fixed-anchor `ColorTransfer` to clip-0 (a 3rd drift lever).
