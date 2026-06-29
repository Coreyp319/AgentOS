# ADR-0053: Read-only-first Check-ins + the deferred write-action seam

- Status: **Proposed** — built this session (Phase A, the read-only floor). The charter for the
  Check-ins tab (ADR-0050) and the seam the future write phase wires into.
- Date: 2026-06-29
- Deciders: Corey (binding product steer: "we'll need many ADRs"; "at the very least" a buildable
  read-only floor), design synthesis from three Plan agents.
- **Reaffirms + extends ADR-0012 §6** (all write/steer actions deferred behind real dependencies) —
  this ADR names the concrete deferred **seam** the Check-ins design implies. Relates ADR-0021 (agent
  act-tier clamp / identity binding), ADR-0005 (reversibility / ledger), ADR-0003 (fail-open).
  **Hands off to a future ADR-0054** (the agentosd→Hermes write client) for the actual wiring.

## Context

ADR-0012 §6 defers every write/steer action (approve `needs_you` / pause / cancel / revert) behind
its real enabling dependency — model proposes, code disposes, reversible-by-default. The Check-ins
design naturally *imagines* steer affordances on each card: **Auto-pilot** (standing autonomy),
**pause**, **run-now**, **approve**. And the enabling dependency now exists but is **not wired**: the
ADR-0012 2026-06-19 amendment found Hermes' full REST control API **coded but not bound** (default
port **8642**, `API_SERVER_PORT`, not enabled in `~/.hermes/config.yaml`). So the actions are
*designable* today but not *callable* today. The risk is shipping disabled buttons that read as
broken, or — worse — smuggling a write path into a read-only Phase A.

## Decision

1. **Phase-A Check-ins is strictly read-only (reaffirm ADR-0012 §6).** No write path, no
   non-determinism, enters this phase. The instrument observes; it does not steer.

2. **Define the seam now, render it honestly.** The action controls + the **Auto-pilot toggle**
   render **disabled**, in the instrument's existing **dim token**, with an honest **"why"
   affordance**: a `ToolTip` / `Accessible.name` reading *"Read-only — write actions arrive with the
   Hermes write-API."* This **reuses the existing footer "Board unavailable" disabled-with-reason
   idiom** the keyhole already ships — a disabled control that *explains itself* rather than a dead or
   absent one. The **local poke / creature delight (ADR-0052) stays the one live interaction** —
   because it mutates view-state only.

3. **Record the affordance→endpoint map now, so the later wiring is mechanical** (the live API
   surface from the ADR-0012 amendment):
   - **pause / cancel** → `POST /v1/runs/{id}/stop`
   - **run-now / create** → `POST /v1/runs`
   - **approve** → `POST /v1/runs/{id}/approval`
   - **Auto-pilot** → a **higher-trust standing-autonomy verb**, gated **additionally** on **ADR-0021**
     (agent act-tier clamp + identity binding) **and ADR-0005** (reversibility / ledger). It is not a
     basic steer; it authorizes *ongoing* action, so even within the future write phase it lands
     **after** the basic pause/cancel/approve verbs — never first.

4. **The enforcement boundary is the box, never the client** (the ADR-0043 spine, inherited): when
   the write phase lands, the disabled controls light up only behind the agentosd→Hermes client
   (ADR-0054), which holds the model-proposes / human-confirms POST — not an autonomous write, and
   not a control the read-only producer ever issues.

## Consequences

- **No new write-path or non-determinism enters Phase A** — the ADR-0012 §4 invariant ("no
  non-determinism in state-mutating code") is preserved because there *is* no state-mutating code
  here; the seam is disabled chrome + an honest reason.
- **The seam makes the future write phase a wiring job, not a redesign.** The UI affordances, their
  endpoints, and their trust gates are decided now; Phase B builds **one shared agentosd→Hermes POST
  client** (reserve **ADR-0054**) and flips the disabled controls live behind it — no surface rework.
- **The same client unblocks two pre-existing gates** that cite the identical Hermes-write-API
  dependency: **ADR-0019 Phase-3** (the Hermes-mirror write) and **ADR-0020's act-verbs**. Building
  it once for Check-ins discharges the ADR-0012 **item-3** pre-`Accepted` gate across all three.
- Until then the disabled-with-reason controls are honest (they tell the user *why*, accessibly), so
  the read-only floor never reads as broken — the calm/honest §7 contract holds.

## Status / next

Proposed; Phase A is the read-only charter + the disabled, self-explaining seam. The wiring is
**ADR-0054** (the agentosd→Hermes write client, **Phase B** — enable `platforms.api_server` on port
**8642**, build the shared POST client, flip the basic steer verbs live; Auto-pilot follows behind the
ADR-0021 + ADR-0005 gates). tok/s + the cost/$budget tile on a card are **ADR-0055** (Phase C, when
the ADR-0002 proxy lands). Recurring/cron cadence detail is **ADR-0056**, though its source already
exists (`~/.hermes/cron/jobs.json`, surfaced by ADR-0051's `recurring` block) so it may fold into
0051.
