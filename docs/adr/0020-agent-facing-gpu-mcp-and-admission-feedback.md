# ADR-0020: Agent-facing GPU surface — an MCP tool over the lease + feedback-driven admission

- Status: **Proposed** (draft — model proposes, code disposes; the human disposes on this)
- Date: 2026-06-16
- Deciders: pending human + resource-safety-reviewer + determinism-safety-reviewer +
  responsible-ai-privacy-skeptic
- Relates to: ADR-0001 (substrate, not orchestrator), ADR-0002 (configure Ollama, don't
  reinvent residency), ADR-0003 (fail-open supervised), ADR-0010 (the VRAM coordinator / lease
  this extends), ADR-0012 (keyhole legibility instrument — the perceive surface this exposes),
  ADR-0013 (owned-spawn via allowlisted profiles, no caller-supplied binary), ADR-0018 (warm-pool
  / heavy-lane partition + learned footprint), ADR-0019 (reviewable request queue — the
  no-prioritization invariant this must honor). Supersedes nothing.
- Research input: `docs/research/0011-gpu-memory-rust-crate-matrix.md` (verified deep-research,
  2026-06-16) — Layer-3 findings: the read-GPU-state-to-MCP pattern (mcp-system-monitor) and the
  CONCUR agent-level AIMD admission design (arXiv 2601.22705).

## Context

The substrate can already *perceive* and *arbitrate* the GPU, but the **agents that consume it
cannot see or reason about that state.** Today:

- `agentosd lease` serves `org.agentos.Coordinator1` on the session bus —
  `Acquire(tier,est) → (granted,token,outcome)`, `Spawn(tier,est,profile,params)` (allowlisted
  profiles only, ADR-0013), `Release(token)`, `Renew(token)`, `Status() → (held,tier,token,free_mib)`.
  Tiers: `Interactive > Batch > BestEffort`. The Hermes plugin glue lives at
  `integrations/hermes/gpu-coordinator`.
- `agentosd keyhole` writes `keyhole.json` (lease/VRAM/residency/fleet) for the tray instrument.
- `agentosd telemetry`/`coexist` (ADR-0018) record history and *propose* a residency plan with a
  per-model learned footprint that corrects Ollama's `size_vram` undercount.

What's missing is the **agent-facing seam**. When a Claude Code or Hermes agent triggers local
inference or a heavy job, it has no way to ask "is the GPU free? what's resident? will this fit?
why did my request wait or get preempted?" The research confirms (a) the perceive pattern is
proven (mcp-system-monitor exposes VRAM to LLMs via an MCP `get_gpu_info` tool — but it is Python
and **read-only**), and (b) CONCUR supplies a citable admission-feedback design: treat the
GPU-resident KV cache as a shared finite resource and regulate admission at the **agent** (not
request) level via AIMD congestion-control feedback.

This serves all four augmentation goals from research 0011: (a) more reliable runs, (b)
agent-controllable GPU, (c) faster inference (fewer blind retries / OOM restarts), (d) legibility.

**The hard constraint.** "Agent-controllable GPU" collides head-on with **model-proposes /
code-disposes** (ADR-0001) and **fail-open-supervised** (ADR-0003). An agent must never be able to
self-grant a lease that induces an OOM, self-`Spawn` an arbitrary binary, or self-SIGKILL a peer.
The whole point of the substrate is that *code* disposes. So the action surface exposed to an agent
must be strictly narrower than the raw D-Bus verbs.

## Decision

Build the agent-facing layer in **two phases**, perceive before act, and gate every action through
the existing deterministic admission/arbitration core — the agent proposes intent, `coord::{admit,
arbitrate}` disposes.

### 1. A Rust MCP server over the existing substrate state (`agentosd mcp`)

Reimplement the proven pattern in Rust over what we already produce — **do not fork the read-only
Python `mcp-system-monitor`, and do not add a Rust GPU crate** (research 0011: `nvml-wrapper` is
already the right and only crate we need; the MCP layer reads `keyhole.json` + calls
`Coordinator1.Status`, it does not touch NVML directly). MIT, local-only transport (stdio /
loopback), no network listener.

Tool surface, in two tiers of trust:

- **Perceive (query-only, always safe).** Every verb returns a typed `status` (`ok` | `unavailable`)
  so an agent can never read a dead substrate as a free GPU (the ADR-0003 fail-open-*inversion* trap).
  - `gpu_status` → free/used/total VRAM, the lease **tier**, and resident models (from `keyhole.json`).
    v1 reports the tier only, **not the holder's identity** — naming who holds the card is identity
    information that needs the act-phase plumbing (ADR-0021), so it is deferred to v2.
  - `gpu_residency` → the warm-pool/heavy-lane view + each model's *learned* admission footprint and
    its confidence (ADR-0018 `coexist`), so an agent can reason about "will my model fit warm or force
    a swap?"
  - `gpu_why` → the legibility verb (goal d): a **system-level** plain-language summary of the last
    contention, sourced from `keyhole.json`'s `lease.preempt` narration + the telemetry signals —
    **never generated**. Three honest states, not two: `unavailable` (blind), a first-class calm line
    (the card was clear, never a bare null), or the recorded preempt narration verbatim. Per-caller
    phrasing ("why did *my* request wait?") awaits a per-request correlation id (open-Q3).

- **Act (intent-only, code disposes):**
  - `gpu_request(tier, estimate_mib)` → a thin, identity-scoped wrapper over `Acquire`. It returns
    the **real admission decision** (`granted | queued | denied` + reason) computed by the existing
    `admit`/`arbitrate` — the agent cannot bypass admission, raise its own tier beyond a
    configured ceiling, or preempt out of band. The grant is the same token machinery; the agent
    still must `Release`.
  - `gpu_release(token)` → wrapper over `Release` for a token the caller owns.
  - **Not exposed to agents:** raw `Spawn` of arbitrary argv (already blocked by the ADR-0013
    profile allowlist — agents get, at most, allowlisted *profiles*, never a binary path), and any
    direct SIGKILL/preempt verb. Eviction stays a code decision driven by tier + admission.

  **Tier ceiling (determinism guard) — a CORE transform, not a shell check.** An agent-class
  caller's tier is clamped to a configured maximum (default `Batch`) *inside* the admission core
  (`CallerClass::clamp` in `coord`, applied in `lease::do_acquire` **before** `arbitrate` sees it),
  so a second D-Bus client cannot bypass it the way an MCP-layer check could. `Interactive` — the
  only tier that preempts — is therefore not self-assertable by an autonomous agent on any transport.
  Implemented + pinned by test now (ADR-0021 GO-1); WHO counts as an `Agent` is the GO-2 identity
  question the `act` verbs still depend on.

### 2. Feedback-driven admission (CONCUR-style AIMD) — Phase 2, conditional on a real signal

Adopt CONCUR as **design inspiration, not a dependency** (it is a Jan-2026 research preprint, not an
MIT crate). The current admission gate is a static predict-before-load check against the learned
footprint. Phase 2 would add a **feedback controller** that modulates how many agent-tier leases are
admitted concurrently, using a runtime **congestion** signal, AIMD-style (additive increase while
pressure is low, multiplicative back-off when it spikes), at **agent granularity** (admit/hold the
agent, not the individual request — matching Hermes/Claude Code's natural unit). CONCUR frames that
signal as KV-cache pressure; which signal is actually reachable here is resolved below.

Two guard rails make this compatible with the existing decisions:

- **It regulates the admission *count*, never the *order* (ADR-0019 invariant).** ADR-0019's
  deferral buffer "MUST NOT order, prioritize, or express dependencies across requests," and
  `lease.rs` deliberately declined a wait-queue. A CONCUR controller that only gates *how many*
  agents are admitted at once — leaving ordering untouched — is **compatible**. If a future design
  needs ordering/priority, it must **supersede ADR-0019 explicitly**, not smuggle it in here.
- **It is conditional on a usable feedback signal — and the literal KV signal is NOT available
  (resolved 2026-06-16).** Investigation (probe of the live Ollama 0.30.7 + primary sources): Ollama
  exposes **no** runtime KV-cache-pressure signal — `/api/ps` is static (`size_vram`, `expires_at`),
  there is no `/metrics` (Ollama issue #3144, open since 2024), and it does not surface llama.cpp's
  `/slots` (issue #6670, closed undelivered). llama.cpp itself has no merged `kv_cache_usage_ratio`
  metric yet (PR #24010, unmerged as of 2026-06). The only path to a true KV signal is running
  `llama-server` standalone — an architectural move that cuts against ADR-0002 (Ollama owns the
  runtime) and is explicitly **out of scope**.
  **Decision:** Phase 2, if it ever ships, keys its AIMD loop on signals already reachable *without
  reinventing anything* — **in-flight request count at the existing axum proxy** (congestion depth)
  and **NVML free-VRAM headroom** (the resource KV pressure ultimately consumes, already read by
  `keyhole`/`telemetry`) — a CONCUR-*shaped* controller on observable congestion, not the literal KV
  ratio. Absent even that, the fail-open default stands: keep the static learned-footprint admission
  (already correct). **No controller is built now.**

### 3. Sequencing

Perceive (1, the query tools) ships first and alone — it is pure read, zero new risk, and delivers
goals (b-partial) and (d) immediately. The intent verbs (1, act) follow once the tier-ceiling +
identity-scoping are reviewed by the determinism + privacy reviewers. The CONCUR controller (2) is
gated behind both a confirmed live signal and a re-rating.

## Consequences

**Positive.** Agents gain honest GPU legibility and a *safe* way to express resource intent without
the substrate ceding control. No new GPU crate, no forked Python tool, no new privileged surface
(reuses `nvml-wrapper` + `keyhole.json` + the existing lease). The CONCUR framing gives a principled,
citable path to fewer stalls under contention when a signal exists.

**Negative / risks.** (a) An MCP action surface is new attack/abuse surface — mitigated by
query-first, tier-ceiling, identity-scoping, allowlist-only spawn, and no raw eviction verb. (b)
The `gpu_why` explanation must be *sourced from telemetry*, never plausibly-generated, or it becomes
confident-but-wrong legibility (a determinism trap). (c) Phase 2 risks reinventing a scheduler if
the count-vs-order line blurs — the ADR-0019 invariant is the explicit tripwire.

**Reversibility.** The MCP server is an additive `agentosd` subcommand + a Hermes/Claude Code config
entry; removing it is deleting the config line. No state migration. The CONCUR controller, if built,
is a feedback wrapper around the existing static gate and falls back to it.

## Implementation status (2026-06-16)

- **Done — Phase 1 perceive** (§1, query tools): `agentosd mcp` — `crates/agentosd/src/mcp.rs`. A
  minimal MCP server (JSON-RPC 2.0 over stdio, read-only, no NVML/D-Bus/network) exposing `gpu_status`
  / `gpu_residency` / `gpu_why`, sourced from `keyhole.json` + the ADR-0018 `coexist` plan + telemetry
  signals (`gpu_why` is sourced, never generated). Verified end-to-end against live data. The act verbs
  are deliberately absent (the test pins their absence). Status of the ADR overall stays **Proposed** —
  this is the zero-risk read slice §3 sequences first; the `act` surface still awaits the human +
  determinism + privacy review.
  - *Council gaps closed (2026-06-16):* every verb now returns a typed `status` (`ok`|`unavailable`)
    so a dead substrate can't read as a free GPU; `gpu_status` no longer exposes the holder identity
    (deferred to v2); `gpu_why` distinguishes blind/calm/narration; and each verb has an exact-string
    contract test pinning its JSON shape (`mcp.rs` — 10 tests, pure shaping split from IO). The
    remaining council "seven prose additions" (e.g. `busy_retry` naming, `rmcp`, warm-hue tripwire)
    attach to the **act** phase and are tracked in `docs/design/0020-agent-facing-gpu-council-brief.md`.
- **Next:** the intent verbs (`gpu_request`/`gpu_release` with tier ceiling + identity scoping, open-Q2)
  after review; Phase 2 (CONCUR AIMD) remains blocked on open-Q1 (a runtime KV-pressure signal).

## Alternatives considered (from research 0011)

- **Fork `mcp-system-monitor` (Python, read-only).** Rejected: read-only (no act, no legibility of
  *our* lease), Python (a second runtime), and it duplicates state we already produce.
- **Expose the raw `Acquire/Spawn/Release` verbs to agents.** Rejected: violates
  model-proposes/code-disposes — an agent could self-grant an OOM-inducing or desktop-preempting
  lease. The intent wrappers + tier ceiling are the disposing-code boundary.
- **Add a Rust GPU/CUDA crate (`gpu-allocator`, `cudarc`, DCGM) for this layer.** Rejected: wrong
  layer / unnecessary (research 0011 matrix) — the agent-facing layer is pure coordination over
  existing reads, not allocation or per-process accounting.
- **Build the CONCUR controller now.** Deferred: no confirmed runtime KV-pressure signal yet, and a
  premature controller risks the ADR-0019 ordering line. Static learned-footprint admission already
  covers the common case.

## Open questions (carried from research 0011)

1. ~~Does Ollama/llama.cpp expose a runtime KV-cache-pressure signal usable as the AIMD feedback
   input?~~ **Answered (2026-06-16): no.** Ollama exposes no `/metrics` (issue #3144) and no `/slots`
   (issue #6670); `/api/ps` is static; llama.cpp's `kv_cache_usage_ratio` is unmerged (PR #24010). A
   true KV signal needs standalone `llama-server` (out of scope, anti-ADR-0002). Phase 2's signal
   therefore pivots to proxy in-flight count + NVML VRAM headroom, or stays deferred (§2).
2. Identity model for the MCP `act` verbs: how does the server scope a token to a caller across the
   Claude Code ↔ Hermes ↔ agentosd boundary so one agent can't `Release` another's lease?
3. Is the `gpu_why` telemetry event log rich enough to explain a specific caller's last wait, or does
   the keyhole/telemetry schema need a per-request correlation id?
4. ~~Does `nvml-wrapper` 0.10 expose `nvmlMemory_v2_t` (the device-wide `reserved` field)?~~
   **Answered (2026-06-16): no.** It exposes only `Device::memory_info()` (v1 total/free/used); the
   device-wide `reserved` field would need a newer nvml-wrapper or raw FFI. Not worth pursuing —
   `free_mib` is already device-accurate; the lossy part is per-process attribution, which `reserved`
   would not fix.
