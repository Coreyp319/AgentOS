# ADR-0007: Cocovox — evaluated, not adopted; harvested as a reference implementation

- Status: Accepted
- Date: 2026-06-15
- Relates to: ADR-0001 (substrate, not orchestrator), ADR-0002 (gateway), ADR-0005 (apply/rollback), ADR-0006 (Hermes plugin, no fork)

## Context
Cocovox (`~/Documents/Cocovox/cocovox-development`) is the author's own shipping
EdTech product — a K-12 / SLP voice-tutoring app, ~846k LOC backend across ~38 sub-apps
+ a 2.4k-file SvelteKit frontend, **forked from Open WebUI v0.3.21** (BSD-3-Clause).
The hypothesis was that it "can do much of what Hermes can do," so it might **replace
Hermes as the orchestrator**, or be **forked** to serve AgentOS, and that its
data-flywheel + learning-science architecture could serve new stakeholders (the Nimbus
"dreaming" composer; a user-facing learning/metacognition layer).

Two multi-agent census passes (2026-06-15; see the `cocovox-evaluation` and
`cocovox-harvest-catalog` memory notes) established:

- **It is not an orchestrator.** No kanban/task engine, no fleet-liveness daemon, no
  generic LLM-pipeline plugin hooks. Its real agent loop (LangGraph supervisor +
  subagents) is genuine but hardwired to tutoring (events typed `LearnerAudioEvent`,
  prompts inject IEP goals, skills keyed by `course_id`).
- **It is cloud-welded.** The agents run on ~17 bare `ChatAnthropic` constructions + an
  Anthropic-only `LLMGateway`, coupled to the Anthropic wire format (streaming
  content-blocks, `cache_control`, `thinking`, server-tool/skills/files betas). The
  inherited Open WebUI Ollama router was **deleted** (not merely disabled); localizing
  the agent runtime is an L-effort rewrite. Adopting it as desktop infra is a category
  error and would import a child-PII compliance surface.
- **It carries landmines.** Live credentials are committed at current HEAD (Google OAuth
  secret, Pexels key) with un-rewritten history; a stored-XSS (`SEC-001`) is unfixed in
  `main`. License is BSD-3 (was mislabeled MIT, corrected).
- **But it is a rich pattern library**, and — unexpectedly — several patterns land on
  AgentOS's substrate critical path, not only the ambient/learning layer (a working
  transparent `/v1` proxy, ollama-aware circuit breaker + provider chain, a
  migration-safety apply/rollback shape, a fail-closed guardrail-flag ledger, a
  race-safe budget/cost-ceiling = the VRAM-yield shape).

## Decision
1. **Do NOT adopt or fork Cocovox** as the orchestrator, nor take a code dependency on
   it. Hermes stays the brain (reaffirms ADR-0001 and ADR-0006).
2. **Treat Cocovox as a reference implementation / pattern library.** Harvest by
   **clean-room reimplementation in Rust** — which sidesteps both the BSD-3 attribution
   burden and the committed-secret repo entirely (no files are cloned or copied).
3. **Record the harvest** as `docs/cocovox-harvest-backlog.md`, mapping each pattern to
   its target milestone (S2/S3, P1/E1, the dreaming composer, the learning layer, dev
   process). Adopt the doctrine the harvest shares with AgentOS's own ADRs.

## Consequences
- The substrate (Phase B) is **de-risked, not re-scoped**: the proxy + breaker +
  provider-chain inform S2; migration-safety + the flag-ledger inform S3. The plan's
  sequence is unchanged; the cost of what's already planned drops.
- Phase A (ambient) and the new learning layer gain a validated source of concrete
  patterns (flywheel bus + effectiveness loop + the topic→mastery→decay→explain chain;
  the shipped ambient-UI vocabulary).
- Nothing is copied out of the Cocovox repo until its secrets are rotated and history is
  scrubbed (an action for Cocovox regardless of AgentOS). Pattern reuse via Rust
  reimplementation is unaffected and is the default mode.
- Cocovox may still run *as an app the desktop coordinates*; it is never the floor.
