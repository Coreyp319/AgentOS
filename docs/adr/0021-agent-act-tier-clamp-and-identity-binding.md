# ADR-0021: Agent act-phase GO conditions — tier clamp in core + identity-bound tokens

- Status: **Proposed** — gates the ADR-0020 act phase. **GO-1 (tier clamp in core) is implemented +
  pinned by test** (2026-06-16); **GO-2 (identity binding) remains open.** Model proposes, code
  disposes; the human disposes on this.
- Date: 2026-06-16
- Deciders: pending human + determinism-safety-reviewer + resource-safety-reviewer +
  wayland-computeruse-reviewer
- Relates to: ADR-0001 (model-proposes/code-disposes), ADR-0010 (the lease/admission core this
  hardens), ADR-0013 (B4 peer-binding — the mechanism reused here), ADR-0020 (the agent-facing
  GPU surface whose **act** phase this gates). Supersedes nothing; it is a precondition.
- Origin: `docs/design/0020-agent-facing-gpu-council-brief.md` §5 gap items 1 + 2 (the two
  GATES-ACT conditions); rating verdict ITERATE, act-as-written ~6 because the central safety
  claim lives in the wrong layer.

## Context

ADR-0020 ships the **perceive** phase (`mcp.rs`, read-only) and gates the **act** verbs
(`gpu_request`/`gpu_release`) behind a determinism + privacy review. The council review found the
act phase's central safety claim — the tier ceiling that stops an agent preempting the desktop — is
phrased as an **MCP-shell property** (ADR-0020 line 84) while the code that would enforce it is
permissive:

- **No tier clamp in core.** `Tier::from_arg` (`crates/agentosd/src/coord.rs:53-60`) accepts
  `interactive` from *any* caller. A clamp in the MCP shell is bypassed by a second D-Bus client
  calling `Acquire(interactive, …)` directly. A guard in front of the trusted boundary is not a
  guard. `arbitrate` (`coord.rs:129-135`) then correctly preempts on strictly-higher tier — which
  is exactly why an unclamped `Interactive` from an agent would SIGKILL-preempt the desktop.
- **No identity binding on release.** `release` (`crates/agentosd/src/lease.rs:169-176`) is guarded
  only by the monotonic token, not by caller identity; `install` mints sequential tokens
  (`next_token += 1`, `lease.rs:193-194`), so a foreign token is *guessable*. Because the MCP server
  flattens many agents behind one bus connection, `gpu_release(token)` is a cross-agent DoS primitive
  — agent A can release agent B's lease. The mechanism to fix this already exists: `holder_peer`
  (`lease.rs:227-230`, ADR-0013 B4) binds a token to the D-Bus unique name.

These are the two GO/NO-GO conditions on the act phase. Neither blocks perceive-v1.

## Decision

**No act verb (`gpu_request`/`gpu_release`) ships until both conditions are met and pinned by test.**

### GO-1 — Tier clamp is a core transform, not a shell check

An agent-class caller's requested tier is clamped to a configured maximum (default `Batch`) **inside
`coord`/`lease`, before `arbitrate` sees it** — e.g. `Tier::clamp_agent(self) -> Tier` applied at the
admission boundary, or a `caller_class` parameter on `do_acquire`. `Interactive` (the only tier that
preempts) is not self-assertable by an autonomous agent regardless of which transport it arrives on.

- **Pinned test:** an agent-class `Interactive` request installs as `Batch`; against a held
  `Interactive` (or `Batch`) incumbent, `arbitrate` returns `Queue`, never `Preempt`.
- **ADR-0020 edit:** rewrite lines 84-87 to state the clamp is a core transform configuring the
  blessed admit/arbitrate core, not an MCP-shell assertion.

**Status: DONE (2026-06-16).** Implemented as a core transform: `Tier::clamp_to`/`Tier::clamp_agent`
+ `CallerClass {Trusted, Agent}` in `coord.rs`, applied in `lease::do_acquire` **before** admission
or arbitration. The existing D-Bus `Acquire`/`Spawn` pass `CallerClass::Trusted` (tier unchanged —
no behavior change today). Pinned by three tests: `coord::tests::{agent_class_clamps_interactive_to_batch_and_leaves_lower_tiers,
a_clamped_agent_interactive_request_can_never_preempt}` and
`lease::tests::an_agent_clamped_request_queues_behind_interactive_instead_of_preempting`. ADR-0020 §1
tier-ceiling paragraph rewritten as a core transform. The `Agent` variant is `#[allow(dead_code)]`
until the first `act` verb constructs it (gated on GO-2).

### GO-2 — Each act token is bound to its MCP-session identity

Reuse the `holder_peer` B4 peer-binding so a token can only be released by the caller identity that
acquired it. The unresolved granularity — the Claude-Code ↔ Hermes ↔ agentosd-MCP-session hop, where
many agents may share one bus connection — must be resolved so the server can attribute a token to a
*session*, not just a bus name.

- **Pinned test:** agent A cannot `gpu_release` agent B's token (foreign-token release returns the
  same failure as an unknown token, with no state change).
- **Honest outcome words (from ADR-0020 T7):** `granted` / `busy_retry` (NOT `queued` — `lease.rs`
  has no wait-queue; a loser must retry, it does not hold a place) / `denied(short_mib)`.

### Out of scope (deferred, not gated here)

- The per-request **correlation id** for per-caller `gpu_why` is a *legibility* refinement, not a
  safety gate (council Q2, recommendation A): act ships with system-level `gpu_why`; per-caller
  phrasing follows when the correlation id lands.
- The `acting` (state 3) felt-state design — escalated to the human (council Q1), reserve-and-defer
  recommended; earns its own ADR + moodboard, not a side effect of this resource work.

## Consequences

**Positive.** The headline safety claim ("an agent can't preempt the desktop or release a peer's
lease") becomes a code-enforced invariant pinned by test, rather than prose asserted in the wrong
layer. Lifts the act phase from ~6 toward ~9 (council rating) and converts the "agent-controllable
GPU" market edge from VISION to PROVEN. Reuses shipped mechanisms (`arbitrate`, `holder_peer`) — no
new GPU crate, no new privileged surface (don't-reinvent intact).

**Negative / risks.** Slower to ship act than the bare wrapper would be. The session-identity
granularity across the Claude-Code↔Hermes↔MCP hop is genuinely unresolved and may require
wayland-computeruse-reviewer + a small spike before GO-2 closes.

**Reversibility.** Both changes are additive to the core (a clamp transform; a peer-binding check
already present for owned jobs, extended to acquired tokens). Removing the act verbs is deleting the
MCP config line; the clamp and binding are inert without an agent caller. No state migration.

## Open questions

1. Session-identity granularity: does the MCP server get a distinct bus name per agent session, or
   must it carry a session token through `gpu_request`/`gpu_release` to attribute to a session behind
   a shared connection? (Blocks GO-2.) → wayland-computeruse-reviewer + resource-safety-reviewer.
2. Default tier ceiling: `Batch` for all agents, or a per-profile ceiling (some trusted agents at a
   higher cap)? Default `Batch`; per-profile is a later refinement, not gated here.
