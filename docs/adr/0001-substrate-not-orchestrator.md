# ADR-0001: AgentOS is a substrate, not a new OS or orchestrator

- Status: Accepted
- Date: 2026-06-15

## Context
The initial framing was "an OS bolstered by local AI with orchestrator capabilities."
Investigation found that the orchestrator already exists on this machine: **Hermes
Agent** (Nous Research, MIT, v0.16.0) is installed and running at `~/.hermes` — a
gateway daemon, a kanban task engine (decomposition, workers, dependency graphs),
delegation/subagents, cron + webhooks, skills organized by category, memory, plugins,
and local Ollama as its default model. CachyOS + the Nimbus pack already provide the
desktop. Building a new OS, a distro, or a second orchestrator would re-derive mature
systems for no gain.

## Decision
AgentOS is **`agentosd`**, a small resource + safety substrate that Hermes and the
Nimbus desktop both run on top of. It is **not** a new OS, **not** a distro, and
**not** a new orchestrator. Hermes stays the brain; Nimbus stays the desktop; AgentOS
is the floor under both.

## Consequences
- Scope shrinks by ~an order of magnitude vs. "build an OS/orchestrator."
- We integrate with Hermes rather than fork it (see ADR-0006).
- Skills live in Hermes' canonical store (`~/.hermes/skills/<category>/`), not here.
- The value delivered is the resource/safety floor, not new agent intelligence.
