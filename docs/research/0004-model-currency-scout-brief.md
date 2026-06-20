# Brief 0004 — model-currency scout: keep dreaming on the best model the machine can run

- Status: Promoted to ADR-0024 (2026-06-19) — extended to the inference LLM + a coexistence-aware fit gate; still unbuilt
- Date: 2026-06-16
- Relates to: ADR-0001 (Hermes owns cron/agents; don't reinvent), ADR-0005 (apply/rollback tx),
  ADR-0006 (Hermes plugin → D-Bus lease), ADR-0009 (dreaming: one ComfyUI backend, SFW Surface A),
  ADR-0010 (VRAM coordinator: `admit()` predict-before-load + owned-PID lease — built),
  ADR-0011 (autonomous overnight pipeline emits *proposals*, never self-applies)
- Reuses (built this session): `agentosd lease` (Spawn/Acquire/Release), `coord::admit`,
  `spikes/dreaming/{dream.sh,comfy_client.py}`

## The need

Dreaming's quality is capped by the model behind it (today: Wan 2.2 14B fp8 + lightx2v 4-step
LoRAs, Hunyuan 1.5, GGUF quants). That ecosystem moves weekly. We want the box to **stay current
automatically**: periodically discover whether a newer/better model exists that *this machine can
actually run*, **validate** a candidate end-to-end, and only then let it serve the overnight
dreaming workflows — without ever silently breaking dreaming or fighting live inference for VRAM.

Two halves:
1. **Discovery** — "what is the latest *relevant* model for this machine?" (judgment-heavy → an agent).
2. **Validation + promotion** — "prove it before sending off the dreaming workflows" (safety-critical
   → deterministic, reversible gates). This is **model-proposes / code-disposes** applied to model
   selection itself.

## Where it lives (don't reinvent — ADR-0001)

- **Hermes owns the cron + the agent + the task/ledger.** This is a **Hermes skill** (e.g.
  `model-scout`) run on a Hermes cron schedule. AgentOS does **not** grow a scheduler.
- **agentosd is the gate, not the brain.** The validation generation is just another **batch lease
  holder**: it calls `Spawn(batch, est, …ComfyUI…)` exactly like `dream.sh`, so it (a) is admitted
  only if it fits (`admit()` predict-before-load), (b) serializes with dreaming + inference, and
  (c) is SIGKILLed + requeued if interactive preempts it. No new coordination code.
- **The canary reuses the real pipeline.** "Validate before sending off" means literally running the
  candidate through `dream.sh` / `comfy_client.py` — the same path production dreaming uses — so a
  pass means the *actual* workflow works, not a synthetic check.
- **Promotion is a reversible config mutation** → routes through the ADR-0005 apply/rollback tx
  (atomic, diffable, revertible). The previous model stays installed for instant rollback.
- **It is an overnight-pipeline citizen** (ADR-0011): runs in the nightly window, and by default
  **emits a proposal** the human triages — it does not silently swap the model out from under you.

## Machine profile (what "relevant" means here)

The scout filters against a declared profile so "relevant" is concrete, not vibes:

```jsonc
{
  "gpu": "RTX 4090", "vram_mib": 24564, "headroom_mib": 1024,
  "loaders": ["UNETLoader","UnetLoaderGGUF","CheckpointLoaderSimple","LoraLoaderModelOnly"],
  "have": { "text_encoder": "umt5_xxl_fp8_e4m3fn_scaled", "vae": "wan_2.1_vae" },
  "families": ["wan2.x-t2v","wan2.x-i2v","hunyuan-video"],   // allowlist of what we track
  "formats": ["safetensors","gguf"],                          // NEVER pickle (.ckpt/.bin)
  "license_allow": ["apache-2.0","mit","cc-by-4.0"],          // hard gate; territory-excluded → deny
  "disk_budget_gib": 120, "keep_versions": 2                  // N-1 retained for rollback
}
```

Relevance = fits VRAM at some runnable quant **and** ComfyUI-loadable format **and** allowed family
**and** reuses available text-encoder/VAE (or bundles them) **and** license clears.

## The cron run (staged pipeline)

Each stage is labelled **agent** (LLM proposes) or **code** (deterministic gate). A stage failing
leaves the incumbent untouched and records *why*.

0. **Profile + state** *(code)* — load the machine profile, the active-model manifest, and a
   `tried-ledger` (candidates already validated/rejected, with reasons) so we don't re-test the same
   model every week.
1. **Discover** *(agent)* — query Hugging Face Hub (and optionally Civitai) for the allowed families:
   filter `pipeline_tag ∈ {text-to-video,image-to-video}`, sort by `lastModified`/trending, read the
   model cards / release notes, and rank candidates *with rationale*. Output: a ranked candidate list
   (repo, revision, claimed footprint, license, why-maybe-better).
2. **Pre-filter** *(code)* — drop anything failing the profile statically: wrong format, license not
   in allowlist (or territory-excluded), no runnable quant ≤ `vram−headroom`, already-active, or in
   the rejected `tried-ledger`. Pick the top survivor(s).
3. **Acquire + provenance** *(code)* — download to a **staging** dir; verify **sha256** (HF LFS
   metadata) and size; confirm the expected repo/author (typosquat guard); record provenance
   (repo@revision, author, SPDX license, constraints). **Reject any pickle format** — safetensors and
   gguf are data-only; `.ckpt`/`.bin` can execute code on load (supply-chain). This is a hard wall.
4. **Fit gate** *(code, agentosd)* — estimate resident footprint for the chosen quant; ask the
   coordinator `admit(free, est, headroom)`. No-fit → reject (predict-before-load; never load-and-OOM).
5. **Canary generation** *(code, under the agentosd lease)* — `Spawn(batch, est, …ComfyUI+candidate…)`,
   run **one short SFW clip through the real dreaming workflow**, fixed seed, with a **time + VRAM
   budget**. The lease serializes it overnight; interactive inference preempts it (SIGKILL → requeue).
6. **Validate output** *(code, + optional vision-judge agent)* — the clip **decodes**, has the right
   dimensions/frame-count, is **not black / not NaN**, finished within the latency budget, and (for
   Surface A) is **SFW**. Optional advisory: a vision/LLM judge scores "calm, coherent, artifact-free"
   vs the incumbent — *proposes*, never the sole gate.
7. **Promote or report** —
   - **Pass** → write the new active-model manifest through the **ADR-0005 tx** (atomic, revertible);
     keep the prior model installed. Default posture: **propose** (a notification / morning triage
     card, ADR-0011) with one-tap accept; *optional* auto-promote when the candidate is a
     same-family newer-version and all gates are green (it's reversible regardless).
   - **Fail** → incumbent untouched; append to `tried-ledger` with the failing gate; honest copy
     ("Found a newer model but it didn't pass validation — dreaming unchanged"), tracebacks to a log
     (ADR-0009 §4).
8. **GC** *(code)* — prune staged/unused models past `disk_budget`, retaining `keep_versions` (N-1)
   for rollback. Log what was dropped (no silent deletion).

## Agent vs code split (model-proposes / code-disposes)

| Concern | Owner | Why |
|---|---|---|
| Which model might be better; reading cards/release notes; ranking | **agent** | indeterminate, judgment |
| Format / license / territory / pickle gate | **code** | safety & legal — must be deterministic |
| VRAM fit | **code** (`admit`) | the substrate's job; prevents OOM |
| Functional canary pass/fail | **code** | objective: decodes? budget? SFW? |
| "Is it nicer than the incumbent?" | **agent (advisory)** | taste; never the only gate |
| Promote / rollback | **code** (ADR-0005 tx) | atomic + reversible |

## Cadence & failure posture

- **Weekly**, inside the ADR-0010 §6 overnight window (releases aren't daily; the canary only burns
  GPU when idle and yields to interactive).
- **Fail-open (ADR-0003):** network down, GPU busy, or any gate uncertain → do nothing; the incumbent
  keeps serving. The scout never blocks or degrades dreaming.
- **Separate tracks:** Surface A (ambient, **SFW-only**, ADR-0009 §2) and Surface B (on-demand,
  imperative NSFW) have *different* allowlists and gates — the scout must not promote an NSFW-only
  model onto the SFW ambient path.

## Open questions (human disposes)

1. **Sources of truth** — HF Hub only (cleanest API + checksums), or also Civitai (more community
   checkpoints, e.g. the Rapid-AIO line, but messier provenance/licensing)? *Lean: HF first.*
2. **Auto-promote vs always-propose** — same-family-newer auto on all-green, cross-family propose-only?
   Or always propose (one-tap)? *Lean: propose-by-default; auto only within a family, behind a flag.*
3. **Quality judge** — ship without it (gates = decodes+budget+SFW) and add a vision-judge later?
   *Lean: yes, ship the deterministic gates first.*
4. **Footprint estimation** — static from quant/param count, or a measured first-load probe recorded
   into the profile? *Lean: estimate to admit, then record the measured peak for next time.*
5. **Trust/signing** — pin repo revisions and keep a provenance ledger; do we want allow-listed
   authors only? *Lean: pin revisions + author allowlist per family.*

## Smallest first slice (when this graduates from brief to build)

A Hermes `model-scout` skill that: reads the profile → HF query for one family (Wan t2v) → pre-filter
→ download+sha256 (safetensors/gguf only) → `Spawn` a canary clip via the existing `dream.sh` path →
validate (decodes + budget + SFW) → **emit a proposal** (no auto-promote). Everything heavier
(Civitai, vision-judge, auto-promote, GC) layers on after that proves out.
