# ADR-0011: Autonomous overnight development pipeline (the persona ensemble as a serialized assembly line)

- Status: Proposed
- Date: 2026-06-16
- Relates to: ADR-0001 (Hermes orchestrates — don't reinvent), ADR-0003 (fail-open supervised),
  ADR-0005 (apply/rollback — model proposes, code disposes), ADR-0006 (Hermes plugin → lease),
  ADR-0010 (VRAM coordinator + overnight lane)
- Tooling: `.claude/workflows/design-council.js`, the ~34-persona ensemble in `.claude/agents/`

## Context

AgentOS already carries a ~34-persona design/review ensemble (design team, reviewers, rating
panel, market team, mediator) and a `design-council` workflow that runs them as
propose/critique → mediate → rate-to-the-bar → brief. This very session ran that ensemble **by
hand, on cloud models** over the dreaming work and produced a scorecard
([0002](../research/0002-dreaming-panel-scorecard.md)), a synthesis
([0003](../research/0003-dreaming-design-synthesis.md)), and two ADRs (0009, 0010) — one full
pass, done manually.

The intent is to make that pass **autonomous, overnight, on local models, serialized to respect
VRAM, and time-boxed** — so a piece of work goes from pre-production to a design-team sign-off
unattended, and a human triages the results in the morning. Interactive use owns the GPU by day;
the ensemble and dreaming share the night through the ADR-0010 lease.

## Decision

1. **Three stages, ~1 hour per job.**
   - **Pre-production (intake):** frame the job — the what/why, scope, and acceptance criteria —
     into a brief the council can act on. (New front stage.)
   - **Design:** the design team propose/critique, with throwaway spikes in `spikes/` where a
     claim needs proving.
   - **Design-team sign-off:** the mediator reconciles, the rating panel gates at the target bar
     (the 10/10 loop), output is a **decision-ready brief + draft ADR(s)**. Stages 2–3 are the
     existing `design-council` workflow; pre-production is added in front.

2. **The bar is the gate; the budget is bounded.** The rate→fix→re-rate loop is already bounded.
   On budget exhaustion or unresolved dissent, the job **halts at "needs human"** with its
   scorecard + recorded dissent attached — it never blocks and never lowers the bar to "pass."

3. **Output is proposals only.** A pass emits a brief, **draft ADRs (Status: Proposed)**, and
   `spikes/`. The pipeline **never accepts its own ADRs, never mutates substrate / desktop /
   system state, and never self-applies.** Model proposes; the human disposes in the morning
   (ADR-0001 reversibility, ADR-0005). This is what makes an unattended overnight loop safe.

4. **Hermes schedules and queues; agentosd does neither.** Hermes' cron fires the nightly run;
   its kanban task engine holds the job backlog (don't reinvent — ADR-0001). agentosd contributes
   only the VRAM lease.

5. **Serialized by the ADR-0010 lease.** Every council agent (local-model inference) and every
   dream generation is a lease consumer — **one heavy GPU consumer at a time**, interactive
   preempts. The night multiplexes council passes *and* dream clips through the one lease; the
   mix per night is tunable.

6. **Local-first, with the sign-off gate as the trust mechanism.** The council runs on local
   models (Ollama via the gateway) by default; a job may be flagged for cloud models when the
   stakes justify it. The target-bar sign-off + recorded dissent + the human accept step are what
   make autonomous local-model output trustworthy.

7. **Fail-open (ADR-0003).** If the pipeline, the lease, or the gateway is down or unsure, the
   night simply produces less. It never blocks interactive use, never half-writes a brief, and a
   failed job is just an absent artifact — re-queued for the next window.

## Consequences

- **This session is the prototype** (cloud, by hand): it produced 0002/0003/0009/0010. This ADR
  formalizes automating + localizing + scheduling that loop.
- **The ensemble graduates** from "on-demand reviewers a human invokes" to "an overnight
  workforce" — which directly answers the earlier scope critique (a 34-persona ensemble is
  overhead *if invoked manually for a solo project*; it pays for itself as an unattended pipeline).
- **Morning ritual:** a human triages the night's output — accept/iterate the draft ADRs, keep or
  discard spikes, merge briefs. The repo's ADR discipline is the accept boundary.
- **Scope discipline:** this is process/tooling (Hermes cron + the workflow + the lease), **not
  agentosd code**. Keep it out of the substrate crate.
- **Primary risk is quality drift on local models**, bounded by the sign-off gate + human accept;
  the loop cannot cause damage because it only emits proposals (decision 3).

## Open questions (human disposes)

- **Intake schema** for pre-production (a brief/PRD shape the workflow consumes).
- **Nightly GPU split** between council passes and dream generation (and how Hermes prioritizes
  the backlog).
- **Local-model selection** for the ensemble (which models per role; cloud-escalation criteria).
- **Where signed-off output lands:** a repo branch/PR, a Hermes kanban card, or `docs/` — and how
  the morning triage surfaces it.
