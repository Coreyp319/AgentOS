# Research 0011 — GPU memory management in Rust: crate matrix for augmenting the agents

- Status: **Research complete (2026-06-16).** Verified deep-research pass (6 angles, 26 sources
  fetched, 124 claims extracted, 25 adversarially verified → 22 confirmed / 3 killed, synthesized
  to 12 findings). No code changed; this is a decision-input note, not an ADR.
- Question: *GPU memory management with Rust to augment our local Claude Code + Hermes agents* —
  end-to-end across (1) Rust substrate mechanics, (2) model-serving memory, (3) agent-facing
  capabilities — landing as a **crate/library comparison matrix** with a recommended pick per job,
  scored for our exact use case (single 24GB RTX 4090, Rust, MIT, fail-open, don't-reinvent).
- Method: fan-out WebSearch → fetch+extract falsifiable claims → 3-vote adversarial verification
  (need 2/3 refutes to kill) → synthesis. Findings carry confidence + vote tallies + citations.
- Relates to: ADR-0001 (substrate, not orchestrator — the don't-reinvent rule this note keeps
  hitting), ADR-0003 (fail-open supervised), ADR-0010 (VRAM coordinator / lease), ADR-0012
  (keyhole legibility), ADR-0018 (VRAM coexistence budget/partition), ADR-0019 (reviewable
  request queue). Codebase anchors: `crates/agentosd/src/{analyze,telemetry,keyhole,coord,main}.rs`.

## Bottom line

The uncomfortable-but-useful conclusion: **most jobs you'd reach for a Rust crate to do, you
should not.** The single biggest pain — the `size_vram` undercount — is **not a measurement bug a
crate fixes**; it is a structural property of the CUDA caching allocator. The one job that genuinely
needs a Rust GPU crate (`nvml-wrapper`) is already the crate in use. The real build surface sits
**above** the crates: cross-tenant arbitration (have it), an MCP tool over the existing lease (new),
and a feedback-driven admission controller (new, with a strong reference design). This note is
mostly a map of **keep / reject / don't-build-it-in-Rust.**

## The crate / library comparison matrix

Scored 1–5 (5 = best fit) for: single 24GB 4090, Rust, MIT-compatible, fail-open, don't-reinvent.

### A. Rust GPU crates (the things you'd `cargo add`)

| Job | Crate | Capability | Maturity | Safety (unsafe surface) | License | **Fit** | Verdict |
|---|---|---:|---:|---|---|---:|---|
| **NVML device reads** (VRAM used/free/total, util, power) | **`nvml-wrapper` 0.10** | 5 | 5 | unsafe FFI isolated in `nvml-wrapper-sys`; runtime-loads NVML via `libloading`, errors instead of crashing when absent | MIT OR Apache-2.0 ✅ | **5** | **KEEP** — already the exact mechanism (`memory_info()` in `coord.rs:254`, `telemetry.rs:164`, `keyhole.rs:171`, `main.rs:140`). No change. |
| **In-process GPU suballocation** | `gpu-allocator` | 4 (its layer) | 5 | 4 | MIT OR Apache-2.0 | **1** | **REJECT — wrong layer.** Vulkan/D3D12/Metal in-process suballocator; *no CUDA, no NVML, no cross-process.* Only ever relevant to the wallpaper/Plasma graphics footprint. |
| **Direct CUDA alloc / context probe** | `cudarc` | 5 | 4 (0.17.x, active 2025–26) | 3 — author: "a lot of unsafe due to ffi & async"; 3-tier `sys`/`result`/`safe` | MIT OR Apache-2.0 ✅ | **2** | **STANDBY / bench pick.** Correct crate *if* AgentOS ever allocates a probe CUDA context to measure overhead directly. Unnecessary today — we coordinate, we don't run kernels. |
| **Direct CUDA bindings (alt)** | `cust` / Rust-CUDA | 4 | 2 — dormant ~3yr, rebooted Jan 2025, *seeking maintainers*, "still getting the project in order" | 3 | Apache/MIT | **1** | **REJECT for a safety substrate.** Too fragile as a hard dependency. If CUDA bindings are ever needed, prefer `cudarc`. |

### B. Non-Rust tools for the accounting / coordination jobs

| Job | Tool | **Fit** | Verdict |
|---|---|---:|---|
| **Fix the `size_vram` undercount** | *(any Rust crate)* | **0** | **DON'T BUILD IT.** CUDA caching-allocator property (`reserved > allocated` by design) + transient inference/KV buffers. Answer = measured multiplier + headroom buffer — **already implemented** (`analyze.rs`: learned-from-load-delta footprint, `1.45×` fallback, `2.0×` cap). |
| **Per-process VRAM accounting** | NVIDIA **DCGM** | **1** | **REJECT.** Per-process stats need accounting mode → **root** (`DCGM_ST_REQUIRES_ROOT`). Violates the unprivileged fail-open posture. Datacenter overkill for one 4090. |
| **Per-process accounting (alt)** | `nvidia-smi` text parse | **1** | **REJECT.** Fragile text parse + fork-per-poll cost. `nvml-wrapper` + Ollama `/api/ps` is strictly better. |
| **Model residency / concurrency / queueing** | **Ollama config** | **5** | **DON'T REINVENT.** `keep_alive` 5min default; `OLLAMA_MAX_LOADED_MODELS=3` (1 GPU); concurrent load only if it fully fits VRAM; else auto-queue+evict. |
| **KV-cache / partition sizing** | **Ollama config + learned footprint** | **5** | **DON'T REINVENT.** KV cache = `NUM_PARALLEL × CONTEXT_LENGTH`. This *math*, not a crate, drives the warm-pool vs heavy-lane partition. |

### C. Agent-facing layer (Layer 3) — patterns & reference designs, not dependencies

| Job | Reference | Type | Verdict |
|---|---|---|---|
| **Expose GPU state to agents (perceive)** | `mcp-system-monitor` (Python, read-only) | **pattern** | **REIMPLEMENT in Rust, don't fork.** Proves the `get_gpu_info` MCP pattern. We already produce `keyhole.json` + serve `org.agentos.Coordinator1` — wrap those as MCP tools so agents perceive **and** act. |
| **Admission / request-queue feedback** | **CONCUR** (arXiv 2601.22705, Jan 2026) | **design** | **DESIGN INSPIRATION.** KV cache as shared bandwidth; AIMD congestion control; admit at the **agent**, not request, level. Research preprint, not an MIT crate. *See tension with ADR-0019 below.* |

## Findings that drive the matrix (the "why")

1. **The undercount is physics, not a bug** *(high, 3-0)* — PyTorch core dev Zach DeVito: the CUDA
   caching allocator keeps every byte it ever touched cached until explicitly freed, because
   `cudaFree` synchronizes the device and is expensive; so `reserved > allocated` is the *design*.
   Generalizes to Ollama/llama.cpp/GGML via the same `cudaFree`-sync cost (NVIDIA-confirmed), though
   exact ratios differ by runtime. — <https://zdevito.github.io/2022/08/04/cuda-caching-allocator.html>
2. **Ollama OOM-despite-predicted-fit is real and maintainer-confirmed** *(2-1, survived 3 refute
   rounds)* — issue #8377: `memory.required.allocations` < `memory.available`, then `CUDA error: out
   of memory` + SIGSEGV; maintainer attributes it to a temporary inference allocation; remedy is
   `OLLAMA_GPU_OVERHEAD`. Validates fail-open-supervised + headroom. (Caveat: repro is dual-GPU with a
   thin ~300 MiB margin → proves *direction*, not the 1.45× *magnitude*.) —
   <https://github.com/ollama/ollama/issues/8377>
3. **Ollama already owns residency/concurrency/queueing** *(high, 3-0)* — 5-min `keep_alive`,
   `MAX_LOADED_MODELS=3×GPUs`, concurrent load only if it fully fits, else queue-until-evict; issue
   #6950 confirms it does *not* CPU-spill a second model. AgentOS's legitimate value is exactly where
   Ollama can't reach: cross-process arbitration with **non-Ollama tenants** (ComfyUI/batch) via the
   D-Bus lease, and whole-GPU predict-before-load admission. The ADR-0018 warm-pool/heavy-lane
   partition is the genuine not-reinvented gap. — <https://docs.ollama.com/faq>
4. **KV-cache math is the partition lever** *(2-1, both halves source-confirmed)* — `NUM_PARALLEL`
   defaults to 1; KV cache scales `NUM_PARALLEL × CONTEXT_LENGTH`. PR #14120 example: 24GB, NP=4,
   ctx=32768 → 131072-token KV cache. A learned footprint is **valid only for the NUM_PARALLEL it was
   measured under** (ADR-0018 already says this; `analyze.rs` enforces relearn). —
   <https://github.com/ollama/ollama/pull/14120>
5. **Layer 3 has a proven pattern + a strong reference architecture** *(high, 3-0)* —
   `mcp-system-monitor` exposes VRAM to LLMs via an MCP `get_gpu_info` tool (Python, read-only).
   CONCUR supplies the admission design: KV cache as finite shared resource, AIMD window regulated by
   cache-pressure feedback, admitting at the **agent** granularity (Hermes/Claude Code's natural
   unit). — <https://github.com/huhabla/mcp-system-monitor>, <https://arxiv.org/pdf/2601.22705>

## What AgentOS should build (mapped to the four augmentation goals)

- **(a) More reliable runs** → keep `nvml-wrapper`; keep the learned footprint (the `1.45×` is only
  the no-sample fallback — see reality check); set `OLLAMA_GPU_OVERHEAD` as the headroom floor.
  *No new crate.*
- **(b) Agent-controllable GPU** → a **Rust MCP tool over `org.agentos.Coordinator1`** (read state +
  a *constrained* action subset). Highest-leverage net-new build. *Pattern borrowed, not forked.*
- **(c) Faster local inference** → Ollama residency/concurrency config + the warm-pool partition;
  load/swap latency is bounded by VRAM fit, a policy problem we already own. *Config + existing lease.*
- **(d) Legibility** → already emit `keyhole.json`; surface "why did it wait/preempt" through the same
  MCP tool and the keyhole instrument. *Extend, don't build.*

The one architecturally new thing worth an ADR: a **CONCUR-style AIMD admission controller** wired
to the lease, using Ollama/llama.cpp KV-cache pressure as the feedback signal — serving (a) fewer
stalls and (d) legible waits at once.

## Codebase reality check (where this note corrects the raw research)

The deep-research pass slightly *understated* what already exists; recording the truth so we don't
re-build what's there:

- **The footprint is already learned, not a constant.** `analyze.rs` learns each model's real
  footprint from the free-VRAM drop at `load:` events; `DEFAULT_UNDERCOUNT = 1.45` is only the
  fallback when no clean load-delta was captured. Guards already present: `MIN_FOOTPRINT_SAMPLES = 3`
  (a 1-sample footprint is rejected), `UNDERCOUNT_MAX = 2.0` (above that is "a noisy log, not
  physics"), low side pinned to 1.0 by the clean-load filter. So the research's "make it a learned
  function" recommendation is **largely done** — the open work is *coverage* (more models/quants),
  not architecture.
- **AgentOS deliberately declined a prioritizing wait-queue.** ADR-0019 §1 notes `lease.rs` exposes
  `AcquireResult::Queued` but **declines a wait-queue by design**; the reviewable-request-queue is a
  thin two-transition deferral buffer that "MUST NOT order, prioritize, or express dependencies." A
  CONCUR-style admission controller is a *different* thing (a feedback gate on admission, not an
  ordering queue), but the boundary is delicate — **any CONCUR adoption must respect the ADR-0019
  no-prioritization invariant**, or it supersedes it explicitly. Flag for the ADR.

## Caveats (re-verify before acting)

- **Version drift.** Ollama defaults are version-pinned and have changed (NUM_PARALLEL was
  auto-4-or-1 in 2024, now fixed at 1). Re-verify `keep_alive`, `MAX_LOADED_MODELS`, `NUM_PARALLEL`,
  and the KV formula against the *installed* version.
- **`1.45×` is one measurement** (gemma, one NUM_PARALLEL, 2026-06-16) — already treated as a
  fallback, not a law.
- **Caching-allocator evidence is PyTorch-sourced.** The `cudaFree`-sync mechanism generalizes
  (NVIDIA-confirmed), but exact reserved-vs-allocated ratios for GGML were not directly measured.

## Open questions

1. Does `nvml-wrapper` 0.10 expose `nvmlMemory_v2_t` (the device-wide `reserved` driver/firmware
   field), or only v1 used/free/total? If only v1, is a thin v2 shim worth it to subtract driver
   reservation from the admission denominator? *(Small refinement, orthogonal to the per-process
   undercount — quick docs.rs check.)*
2. What is the measured undercount across the *other* warm-pool models, and how does it vary with
   quant (q4_k_m vs q5/q8), context, and NUM_PARALLEL? (Coverage question for the learned footprint.)
3. Can CONCUR's agent-level AIMD bind to the D-Bus lease using a *runtime* KV-pressure signal — and
   does Ollama expose one (vs only static `/api/ps size_vram`)?
4. For the MCP action surface (goal b): full `Acquire/Spawn/Release` verbs, or a **constrained
   subset** (request-priority + query-only) to preserve model-proposes/code-disposes and stop an
   agent self-granting an OOM-inducing lease?

## Refuted (killed by the adversarial pass — recorded so they don't resurface)

- *NVML `used` includes system-reserved, so it's not purely application allocations* — **1-2,
  refuted** (the fields are not strictly additive).
- *Ollama #14751 is a cross-process interference OOM* — **0-3, refuted.**
- *The CUDA failure proves Ollama does no predict-before-load admission and fails open destructively
  mid-execution* — **0-3, refuted.**

## Key sources

- nvml-wrapper — <https://github.com/rust-nvml/nvml-wrapper> · <https://docs.rs/nvml-wrapper>
- CUDA caching allocator (root cause of the undercount) — <https://zdevito.github.io/2022/08/04/cuda-caching-allocator.html>
- nvmlMemory_v2_t (reserved field) — <https://docs.nvidia.com/deploy/archive/R535/nvml-api/structnvmlMemory__v2__t.html>
- gpu-allocator (wrong layer) — <https://github.com/Traverse-Research/gpu-allocator>
- cudarc — <https://github.com/coreylowman/cudarc> · Rust-CUDA status — <https://rust-gpu.github.io/blog/2025/08/11/rust-cuda-update/>
- DCGM per-process stats (needs root) — <https://docs.nvidia.com/datacenter/dcgm/latest/dcgm-api/dcgm-api-process-stats.html>
- Ollama FAQ (residency/concurrency) — <https://docs.ollama.com/faq> · KV cache PR — <https://github.com/ollama/ollama/pull/14120>
- Ollama OOM-despite-fit — <https://github.com/ollama/ollama/issues/8377>
- mcp-system-monitor (MCP GPU pattern) — <https://github.com/huhabla/mcp-system-monitor>
- CONCUR (AIMD admission) — <https://arxiv.org/pdf/2601.22705>
