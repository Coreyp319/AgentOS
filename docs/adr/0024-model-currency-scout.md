# ADR-0024: Model-currency scout — keep BOTH the dream and the inference model on the best the box can actually run

- Status: Proposed
- Date: 2026-06-19
- Relates to: ADR-0001 (Hermes owns cron/agents/model — don't reinvent), ADR-0005 (apply/rollback tx),
  ADR-0006/0010 (D-Bus lease + `admit()` predict-before-load), ADR-0011 (overnight pipeline emits
  proposals), ADR-0018 (VRAM coexistence budget), ADR-0022 (creative-app lanes under the lease).
  Promotes research brief `docs/research/0004-model-currency-scout-brief.md` to a decision.

## Context

Brief 0004 designed a scout that keeps **dreaming** on the best *video* model the box can run:
Hermes-cron skill → discover (agent) → deterministic gates → canary through the real workflow →
reversible, proposal-only promotion. It is sound and still unbuilt. Two things force the decision now:

1. **Scope gap — the inference model was never covered.** The registry (`integrations/models/
   registry.json`) tracks only the creative pipeline (B2 vision, narrator, t2i, i2v). The Hermes
   inference model lives in `~/.hermes/config.yaml` (`qwen3.6-27b-64k`) and nothing — not the
   registry, not the scout — asks whether it is still the right pick. Per ADR-0001 the LLM is
   Hermes's domain, but its **VRAM footprint is the substrate's central concern** (ADR-0018), so its
   currency cannot be a blind spot.

2. **"Best" means "best that runs on GPU under the live load" — measured, not assumed.** On
   2026-06-19, with ComfyUI holding 13.3 GB outside the lease, the 27B inference model loaded **87%
   onto CPU** (2.5 GB of 19.9 GB in VRAM) → prompt-eval 107 tok/s and generation **1.5 tok/s** (a
   208 s turn). A *more capable* model (e.g. Hermes-4.3-36B at 21.8 GB) would have been strictly
   worse — it fits even less. **Capability in isolation is the wrong metric.** A scout that ranked by
   benchmark scores and a fit check against an *empty* card would happily promote a model that then
   thrashes on CPU.

## Decision

1. **Build brief 0004 as written** — Hermes-cron `model-scout` skill; agent discovers + ranks,
   deterministic code gates (format/license/provenance, no pickle), canary through the *real*
   pipeline under the `Spawn(batch,…)` lease, reversible ADR-0005 promotion, **proposal-only**
   (ADR-0011). agentosd is the gate (`admit()` + lease), Hermes is the brain (cron/agent/ledger).
   AgentOS grows no scheduler.

2. **Extend scope to two model classes** under one scout:
   - **(A) Creative/dream models** — brief 0004's original target (Wan/Hunyuan/LTX i2v, t2i).
   - **(B) The inference LLM** — discover newer/quantized local LLMs that fit; the canary is a
     fixed prompt-eval through Ollama; promotion mutates the Hermes model config via the ADR-0005 tx
     (atomic, revertible, prior model retained). Add the inference model to `registry.json` as a
     first-class affiliation so the source-of-truth stops excluding it.

3. **The fit gate is coexistence-aware (the headline).** Replace "fits an empty 24 GB" with:
   - **Admit against *current* free VRAM** under the live load (`admit(free_now, est, headroom)`),
     not the empty card — the candidate must fit alongside whatever the coexistence budget (ADR-0018)
     reserves, or it is rejected/queued, never loaded-and-offloaded.
   - **A CPU-offload tripwire in the canary**: after load, read `size_vram` vs `size` and the
     measured tok/s; **fail any candidate that does not stay ~100% on GPU** (e.g. `size_vram < size`,
     or generation below a tok/s floor). A model on CPU is rejected no matter how capable.

4. **Out of scope here (flagged, not fixed):** the trigger condition — a *persistent* ComfyUI server
   holding 13 GB **outside the lease**, which the interactive `Acquire` cannot evict (it only
   SIGKILLs jobs it spawned). That is an ADR-0018/0022 coexistence gap (bring standing creative
   servers under the lease / an idle-unload policy), not the scout's job. The scout *depends* on it
   being fixed, else even a perfectly-chosen model offloads.

## Consequences

- The box stays current on both axes without silent swaps; every promotion is diffable, reversible,
  and human-triaged by default. The incumbent is never touched unless a candidate passes every gate.
- The scout can only ever *propose* a model that demonstrably runs on GPU under real load — the
  failure mode that bit us (a capable model thrashing on CPU) is structurally excluded.
- Net-new code is a Hermes skill + the registry/manifest plumbing; the coordination (admit/lease/tx)
  is all reused. The standing-server eviction gap is tracked separately against ADR-0018/0022.
