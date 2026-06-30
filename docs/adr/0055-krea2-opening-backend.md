# ADR-0055: Krea 2 Turbo as a reversible, rating-gated opening-image backend

- Status: **Accepted** — built + live-verified, then **activated** (`t2i-engine = krea2`) 2026-06-30 at Corey's direction; rollback to `illustrious` stays one env var away.
- Date: 2026-06-29
- Deciders: Corey (binding steer: *"wire both versions into the lucid dreaming with easy rollback to current known good config"*).
- Relates: ADR-0015 (text-to-opening seed source), ADR-0044 (mature 18+ opt-in + the registry `rating` field), ADR-0040/0033 (keyframe / hero stills), ADR-0001 (the one validated ComfyUI client — do not fork). **Mirrors the `i2v-engine` selector pattern.**

## Context

Lucid's text-to-opening (ADR-0015) renders a dream's opening frame from a typed description, today via a single SDXL model — `t2i-opening` = `divingIllustriousReal` (~28 steps, cfg 5, 768×1344). Krea 2 Turbo — a 12.9B Krea DiT that borrows the Qwen-Image VAE + a Qwen3-VL-4B encoder, runs at 12 steps / cfg 1, installed and verified this session — is a faster, higher-quality opener. Its text encoder ships in **two variants**: a stock Qwen3-VL-4B and an **abliterated** (refusal-direction-removed) Qwen3-VL-4B. The requirement: make Krea 2 available for the opening frame in **both SFW and mature forms**, without changing the current known-good behavior, and with an instant rollback.

## Decision

1. **Add a `t2i-engine` registry selector** (`modality: selector`, `model: "illustrious"`), mirroring the proven `i2v-engine` pattern. `"illustrious"` (default) keeps divingIllustrious; `"krea2"` selects Krea 2 Turbo. **Nothing changes until flipped.**
2. **Rating-gate the encoder, not a separate model.** One Krea DiT; the **dream rating** picks the encoder — stock Qwen3-VL for `sfw`, abliterated Qwen3-VL for `mature`. This lets a mature opening be pose/anatomy-correct (the reason SD-Turbo was replaced, ADR-0015) **without uncensoring sfw dreams**. The deterministic red-line gate (minors / real people / non-consent) and the B2 likeness guard are unchanged and run on both paths.
3. **Two registry image entries** (`t2i-krea2-sfw`, `t2i-krea2-mature`) carry the `fetch` / `rating` / `tier` metadata for the audit panel + onboarding. Encoder filenames are code constants (env-overridable: `LUCID_T2I_KREA_ENC_{SFW,MATURE}`).
4. **Build the Krea graph inline** in `lucid_t2i._workflow_krea()` (ComfyUI API format: `UNETLoader` + `CLIPLoader type=krea2` + `VAELoader` + `ConditioningZeroOut` negative + `EmptySD3LatentImage` + `KSampler` euler/beta/12/cfg1), submitted through the shared validated `comfy_client` (ADR-0001 — not forked). Predict-before-load uses a Krea-sized ~18.5 GB estimate (`LUCID_T2I_KREA_EST_MIB`) so it **refuses rather than OOMs**.

## Reversibility (the explicit requirement)

The selector is **activated** (`krea2`) as of 2026-06-30; reverting to the known-good `illustrious` opener is one of three, increasing in finality:
1. **Runtime env** — `LUCID_T2I_ENGINE=illustrious` overrides the registry instantly (symmetric to the existing `LUCID_ENGINE` / `LUCID_WORKFLOW` reverts).
2. **Registry flip** — set `t2i-engine.model` back to `"illustrious"` + `systemctl --user restart agentos-lucid`.
3. **Literal restore / git** — `integrations/models/registry.json.known-good.bak` is the pre-Krea snapshot; or `git revert` this commit.

## Consequences

- **Pro**: faster / higher-quality openings on demand; mature openings get a faithful encoder; zero blast radius until opted in; the audit panel (:9124) sees the new models.
- **Con / watch**: the Krea opening is **~18 GB resident** — heavier than the SDXL opener — and runs on the on-demand ComfyUI, gated only by predict-before-load. If it contends in practice, a follow-up can move the opening under the VRAM-coordinator lease. The mature encoder is uncensored: it is reachable **only** when (a) the engine is flipped to `krea2` **and** (b) the dream rating is mature; sfw dreams never touch it. Licenses: Krea 2 Community License (personal use); the abliterated encoder is Apache-2.0.

## Testing

`apps/dreaming/lucid/test_lucid_t2i_krea.py` (hermetic, no GPU): default engine is `illustrious`; env flips to `krea2`; the sfw graph uses the stock encoder and mature uses the abliterated one; an unspecified rating defaults to sfw (never the uncensored encoder); graph shape (krea DiT, `type=krea2`, euler/beta, `ConditioningZeroOut`, `EmptySD3LatentImage`) is correct; the illustrious `_workflow` builder is unchanged. Live-verified this session: both ratings render 768×1344 openings through the real `generate_opening()` path on the 4090 (~18 s each, abliterated encoder confirmed for the mature path).
