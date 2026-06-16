# ADR-0006: Hermes integration via a plugin, not a fork

- Status: Accepted
- Date: 2026-06-15

## Context
To make Hermes priority- and lease-aware, `agentosd` needs hooks into it. A source read
of the Hermes plugin system found:
- `llm_request` middleware can tag outgoing inference requests cleanly (supported).
- `llm_execution` middleware can wrap the call to acquire/release a lease (supported).
- A `pre_tool_call` hook can soft-veto further `delegate_task` spawns when saturated.
- **True spawn-gating backpressure** (stop Hermes from spawning workers) has *no*
  supported hook — it would require patching `delegate_tool.py` / `kanban_watchers.py`,
  i.e. a maintained fork. That is the one thing we agreed not to do.

## Decision
Ship a **Hermes plugin** (the supported extension point — not a fork):
- `llm_request` middleware → priority tag (`X-GPU-Priority`).
- `llm_execution` middleware → acquire/release the D-Bus GPU lease around the call.
- `pre_tool_call` → soft veto on new child spawns when the GPU is saturated.
- Do **not** fork Hermes core.

Real backpressure comes for free from the enforcing gateway (ADR-0002) *holding*
inference responses — spawned workers simply block on the call. If spawn-level gating is
ever genuinely needed, `agentosd` tunes kanban `max_in_progress` via the `hermes` CLI
out-of-band.

## Consequences
- Survives Hermes upgrades; no core patches to rebase.
- Priority is best-effort (Ollama FIFO + proxy ordering), not preemptive.

## Implementation status (2026-06-16)
The **agentosd side of the D-Bus GPU lease now exists and has teeth**: `agentosd lease` is the
unified coordinator daemon — it serves `org.agentos.Coordinator1` (`Acquire` cooperative /
`Spawn` owned / `Release` / `Status`) on the session bus AND owns the batch children, so a
higher-tier acquire SIGKILLs the running job (validated live). See ADR-0010's implementation
status and `crates/agentosd/src/lease.rs`. The **Hermes plugin** that calls it (`llm_request`
priority tag, `llm_execution` `Acquire`/`Release` around the call, `pre_tool_call` veto) is
still unbuilt — that is the remaining work for this ADR.
