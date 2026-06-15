# ADR-0003: Fail-open + supervised gateway

- Status: Accepted
- Date: 2026-06-15
- Relates to: ADR-0002 (the gateway), ADR-0005 (correctness is guarded elsewhere)

## Context
Because the enforcing gateway (ADR-0002) sits on the inference path, a crash would take
all local AI on the box down with it — Hermes included. This is a single point of
failure. Enforcement (nothing bypasses arbitration) and availability (AI never goes
dark) are in direct tension.

## Decision
**Fail-open + supervised.**
- On smart-path errors (priority/VRAM logic), the proxy still forwards to Ollama rather
  than rejecting.
- Run under systemd with `Restart=always` and minimal state so restart is near-instant.
- Even in degraded/passthrough mode, fire the graphics-yield reflex before forwarding,
  so most OOMs are still avoided.

## Consequences
- AI never goes dark from a broker bug — the right trade for a daily-driver desktop.
- The "nothing bypasses arbitration" invariant becomes best-effort during rare faults.
- This is acceptable precisely because correctness is guarded *separately* by the
  apply/rollback layer (ADR-0005): a GPU-path fault is a performance failure, not a
  data-corruption failure.
