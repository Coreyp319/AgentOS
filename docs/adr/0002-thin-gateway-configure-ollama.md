# ADR-0002: Inference path — thin transparent enforcing gateway over a configured Ollama

- Status: Accepted — enforcing proxy **DEFERRED** (see Implementation status, 2026-06-19)
- Date: 2026-06-15
- Relates to: ADR-0003 (fail-open), ADR-0004 (VRAM yield), ADR-0006/0010 (the lease — the control plane that actually shipped)

## Context
`agentosd` wants a single enforced point on the inference path for request priority,
metrics, and the VRAM-yield trigger. A build-vs-buy research pass (2026-06-15) found:

- **Ollama already provides** model residency, concurrency, and queueing via
  configuration: `OLLAMA_MAX_LOADED_MODELS` (default 3), `OLLAMA_NUM_PARALLEL`
  (default **1** — must be raised), `OLLAMA_KEEP_ALIVE` (pin `-1` / evict `0`), and a
  512-slot FIFO request queue (`OLLAMA_MAX_QUEUE`, HTTP 503 on overflow).
- A **fidelity spike** (`spikes/proxy-fidelity/`) proved a ~90-line `axum` + `reqwest`
  transparent reverse proxy passes non-streaming, streaming SSE (verified incremental),
  and tool-calls byte-faithfully to Ollama.
- **LiteLLM** is mature but the wrong fit here: it solves 100-provider translation we
  don't need (single backend; Hermes already speaks `/v1`), and it carries a heavy
  Python dependency, a CVE / supply-chain history, and real Ollama-translation bugs
  (tool-call `JSONDecodeError`, dropped streaming `tool_calls`) that our transparent
  passthrough avoids. `ollamaMQ` (Rust, axum+tokio) proves dual `/api`+`/v1` + priority
  is a solved pattern — reference, not dependency. `Pingora` is overkill.

## Decision
1. **CONFIGURE Ollama** for residency / concurrency / queueing — see `config/ollama.env`.
2. Put a **thin transparent enforcing proxy** (the spike, grown) in front for priority
   tagging, metrics, and the VRAM-yield trigger — built on `axum`/`reqwest`.
3. Do **NOT** reimplement a scheduler or a second queue (lean on Ollama's; avoid the
   double-queue footgun). Do **NOT** adopt LiteLLM.

## Consequences
- Small build; the enforcement invariant (single endpoint) is preserved.
- Priority is lightweight: Ollama is FIFO, so the proxy injects ordering ahead of it.
- Per-model streaming + tool-call fidelity must be validated (done for `qwen3.6-27b`).

## Implementation status (2026-06-19) — proxy DEFERRED, lease is the control plane
Verified against the live install:

- **Hermes inference points straight at Ollama**, not at any agentosd proxy:
  `~/.hermes/config.yaml` → `base_url: http://localhost:11434/v1`. Nothing is bound on
  `:11435`. There is **no `proxy` subcommand** in `agentosd` — only the throwaway
  `spikes/proxy-fidelity/` (which proves passthrough fidelity but "is not agentosd").
- Because no enforcing proxy reads it, the Hermes plugin's `llm_request` `X-GPU-Priority`
  middleware is **intentionally left unregistered** (`integrations/hermes/gpu-coordinator/
  __init__.py`); request-level priority is therefore **non-functional today** and Ollama
  remains plain FIFO.
- This is acceptable because the **safety-critical inference-vs-batch control plane is the
  ADR-0006/0010 D-Bus lease** (interactive `Acquire` SIGKILLs the running batch child), and
  that path needs no proxy. The proxy only ever bought request-level *ordering* on top.

**Decision:** keep the enforcing proxy **DEFERRED**. Revisit only when (a) request-level
ordering is genuinely needed *and* (b) something downstream actually honors the priority
header (Ollama does not). Until then the lease is sufficient and the proxy would be a
single-endpoint chokepoint with no consumer. If it is never needed, supersede this ADR.
