# ADR-0021: Agent act-phase GO conditions — tier clamp in core + identity-bound tokens

- Status: **Proposed — design ratified-with-changes (2026-06-21 reviewer panel).** GO-1 (tier clamp in
  core) is **merged** (`fbcc6a8`) + pinned by test. GO-2's **connection-grain** mechanism is **merged**
  (`8c36c26`), but the session-identity invariant it rested on — "one `agentosd mcp` process = one
  agent = one bus name" — is **REFUTED for the installed Hermes path** (Hermes runs one shared
  `agentosd mcp` connection and fans sub-agents out as in-process threads, so they collapse to one bus
  name and GO-2's binding cannot isolate them; verified against `~/.hermes`). GO-2 is therefore
  **reframed** (a three-layer identity model, §Ratification pass) and **NOT closed**: act-verb
  implementation is gated on a session-granularity spike + the must-fix list below. Model proposes,
  code disposes; the human disposes on this.
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

**Mechanism merged + reframed (2026-06-21).** The connection-grain mechanism is built (`8c36c26`):
`may_release` (`lease.rs:463`) refuses a foreign-token release, `do_acquire` binds `holder_peer` to the
acquiring bus name (`lease.rs:795`), and the `Release` verb passes `hdr.sender()` (`lease.rs:1047`).
But the granularity above is **not** resolved by bus-name binding alone: the installed Hermes
multiplexes many sub-agents over one shared `agentosd mcp` connection (§Ratification pass), so they
share one bus name and the binding cannot tell them apart. GO-2's identity is therefore reframed into a
three-layer model (daemon binds the *connection*; the MCP server isolates the *session* in-process;
deployment prefers connection-per-agent) — see §Ratification pass for the resolution + the spike that
gates it.

- **Pinned test:** agent A cannot `gpu_release` agent B's token (foreign-token release returns the
  same failure as an unknown token, with no state change). *Merged at the bus-name grain
  (`go2_release_is_identity_bound_for_cooperative_holders`); the same-connection two-session case is a
  must-fix test — see §Ratification pass item 6.*
- **Honest outcome words (from ADR-0020 T7):** `granted` / `busy_retry` (NOT `queued` — `lease.rs`
  has no wait-queue; a loser must retry, it does not hold a place) / `denied(short_mib)`.

### Out of scope (deferred, not gated here)

- The per-request **correlation id** for per-caller `gpu_why` is a *legibility* refinement, not a
  safety gate (council Q2, recommendation A): act ships with system-level `gpu_why`; per-caller
  phrasing follows when the correlation id lands.
- The `acting` (state 3) felt-state design — escalated to the human (council Q1), reserve-and-defer
  recommended; earns its own ADR + moodboard, not a side effect of this resource work.

## Ratification pass (2026-06-21) — reviewer panel verdict: RATIFY-WITH-CHANGES (5/5)

A five-lens adversarial panel (security, determinism-safety, resource-safety, wayland-computeruse,
responsible-ai-privacy) ratified the GO-2 design + the merged mechanism. Unanimous verdict:
**RATIFY-WITH-CHANGES** — the *direction* is sound (clamp-in-core, `AcquireAgent`-class-by-verb,
`holder_peer` binding), but it was ratified on a false invariant and leaves concrete must-fixes.

### The refuted invariant (the headline finding — empirically verified against `~/.hermes`)

`docs/design/0021-go2-session-identity-binding.md` ratified bus-name binding as "sufficient for v1 …
on one invariant: one MCP-server D-Bus connection per agent session," dismissing the multiplexed-Hermes
case as "a caller that doesn't exist yet." That caller **exists and is installed + enabled on this box
today**:

- `~/.hermes/config.yaml:627` — the `agentos` MCP server is enabled; `inherit_mcp_toolsets: true`
  (:404), `max_concurrent_children: 3` (:408), `orchestrator_enabled: true` (:410).
- `~/.hermes/hermes-agent/tools/mcp_tool.py:2076` — `_servers` is a **module-level** dict keyed by
  server name → exactly ONE `agentosd mcp` child (one stdio connection, one bus name) per Hermes process.
- `~/.hermes/hermes-agent/tools/delegate_tool.py:28,59` — delegated sub-agents are in-process
  `ThreadPoolExecutor` threads, **not** subprocesses, and inherit the `agentos` toolset by default.

So a Hermes parent + up to 3 concurrent thread-children all call through one connection → one bus name
→ `may_release` (`lease.rs:463`) sees `name == bound_name` for *every* sibling → child B can release
child A's lease. The cross-agent DoS GO-2 exists to kill is **open at sub-agent granularity on the exact
orchestrator AgentOS is built around** (ADR-0001). The Hermes *lease plugin*
(`integrations/hermes/gpu-coordinator/lease_client.py`) is a separate Trusted persistent-connection
caller and is **not** affected — do not conflate. The Claude-Code stdio path (subprocess-per-session)
still satisfies the invariant for free.

### Resolution — Open-Q1: a three-layer identity model (supersedes "bus-name binding is enough")

Identity is enforced at the grain each layer can actually see:

1. **Daemon (`holder_peer`, merged).** Binds a token to the D-Bus *connection* (unique bus name).
   Correct and kept — it stops a *different* bus client releasing the MCP server's token. `may_release`
   is right at the grain it sees.
2. **MCP server (NEW — the fix).** The `agentosd mcp` server enforces *per-session* ownership
   **in-process**: at most one lease token per MCP session, and it refuses any `gpu_release` whose token
   that session did not acquire, *before* the D-Bus `Release` fires. The per-session key is ephemeral,
   in-memory, never persisted, never on the wire — so it adds no identifiable state (privacy lens) and
   cannot be self-asserted by a sibling the way a protocol-carried `session_id` could
   (determinism/security lens).
3. **Deployment.** Prefer connection-per-agent where the transport allows (Claude-Code gets it free).

**Closing precondition — a decisive spike (≤ half a day).** Layer 2 is only implementable if the
`agentosd mcp` server can *distinguish* Hermes' in-process thread-children as separate MCP sessions over
the one stdio pipe. If Hermes surfaces them as ONE `ClientSession`, the server is blind too and the only
fix is upstream Hermes (per-child connection). **The spike:** under the installed Hermes (`agentos`
enabled, `inherit_mcp_toolsets: true`), have a parent delegate two concurrent children that each call an
`agentos` tool; instrument the server to log whether they arrive as distinct MCP sessions or one.
**Until the spike clears it, act-verb enablement is scoped to the Claude-Code stdio transport; the
Hermes path is gated.**

### Resolution — Open-Q2: Batch-for-all (unanimous); per-profile deferred to its own ADR

Keep the `Batch` ceiling for every agent — it is the highest tier that cannot preempt the desktop (the
load-bearing GO-1 claim) and requires *no* per-agent identity. Per-profile ceilings reintroduce an
identity-keyed, spoofable-on-the-open-bus policy with no authenticated principal to attach to (A1 is
deferred) and a net-new identity collection (privacy) — they earn a separate ADR with its own authz +
privacy review. Also clamp the agent **floor**: agent class ∈ {`BestEffort`, `Batch`} only — *not*
`Yielding` (reserved for the owned UE-wallpaper profile).

### Must-fix before the act verbs are implemented (ratified)

1. **Identity-bind `Renew`.** `renew` (`lease.rs:1054`) checks only the (sequential, guessable) token —
   the same hole GO-2 closed for `release`. Add `may_renew` mirroring `may_release` + a pinned
   foreign-renew-refused test. (Latent in merged code; not yet reachable — all callers are Trusted —
   but it must close before the first `Agent` caller lands.)
2. **Typed outcome channel, not prose.** `do_acquire` returns free text (and the literal word "queued",
   which §GO-2 forbids surfacing). Add a stable `outcome_code` (`granted|busy_retry|denied|cooling|
   unavailable|error`) + numeric `short_mib`/`retry_after_ms`; the MCP layer maps codes, never strings.
   Map the C7 cooldown to `busy_retry` + `retry_after`, not a bare failure (or an agent hot-loops).
3. **Per-class agent lease TTL (~90 s) + mandatory `Renew` heartbeat.** The 90-min default
   (`lease.rs:120`) is a dream-length backstop; a cooperative agent lease abandoned behind a shared MCP
   connection (B4 can't see one sub-agent die) would wedge the batch lane for 90 min. A short TTL +
   server-driven heartbeat reclaims it in ≤ ~90 s.
4. **Act fails CLOSED.** The MCP→D-Bus call carries a short timeout (never hangs); a down coordinator ⇒
   no grant and the agent must NOT launch heavy GPU work — the *inverse* of Hermes interactive's
   fail-open (an agent has no human to notice an OOM). Decide + document in Consequences.
5. **Unguessable act tokens.** Replace the sequential `next_token += 1` (`lease.rs:257`) with a random
   `u64` for agent tokens, so a shared-connection sibling can't enumerate a live token (defence-in-depth
   behind layer 2).
6. **Session-isolation test at the SERVER layer.** The merged
   `go2_release_is_identity_bound_for_cooperative_holders` test covers only *distinct* bus names — it
   gives false confidence about the shared-connection case. Add a test: two sessions, one connection →
   cross-session `gpu_release` refused.
7. **`holder_peer` no-leak invariant (ratified — pin it).** The bound peer name is release-authz state
   only: never written to `lease.json`/`keyhole.json`, returned from any verb, or logged except the B4
   disconnect line; in-memory, session-lifetime, never persisted (B5 TTL is the retention ceiling). Add
   a producer-side test (the twin of `mcp.rs`'s consumer no-leak test). *Confirmed sound end-to-end
   today; this freezes it as a contract.*
8. **Honesty: cooperative-agent VRAM is not reclaimable on preempt.** Once an agent's cooperative-lease
   process has allocated VRAM, agentosd cannot reclaim it — Interactive wins the *lease* but may still be
   forced to CPU-offload (the 87 %-on-CPU failure mode). State this residual; telemetry-enforced eviction
   (ADR-0018 Phase-3 / ADR-0020 CONCUR) is the deferred fix.
9. **Re-frame A1 from "orthogonal" to "the deferred closer."** The `Trusted` verbs
   (`Acquire`/`Spawn`/`AdoptScope`) remain a same-uid desktop-DoS path the act surface does not introduce
   but sits beside; ADR-0013 A1 (private socket + `SO_PEERCRED`), or the weaker per-call
   `GetConnectionUnixUser == geteuid()` stopgap, is what bounds it. (`AcquireAgent` itself is strictly
   *more* restrictive, so it adds no new harm — but that is not the whole risk story.)
10. **Constrain the future per-caller `gpu_why` correlation id** (already out-of-scope): when it lands it
    must narrate only the *calling* agent's own lost contentions — never name another holder.

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

1. ~~Session-identity granularity: distinct bus name per agent session, or a carried session token?~~
   **Resolved (2026-06-21 panel): neither — a three-layer model** (daemon binds the connection; the MCP
   server isolates the session in-process; deployment prefers connection-per-agent). The bus-name
   invariant the original design rested on is FALSE for the installed Hermes (shared-connection
   thread-fan-out). Closing the layer-2 fix is **gated on a decisive spike** — can `agentosd mcp` see
   Hermes' thread-children as distinct MCP sessions? See §Ratification pass.
2. ~~Default tier ceiling: `Batch` for all, or per-profile?~~ **Resolved (2026-06-21 panel,
   unanimous): `Batch`-for-all**, plus an agent *floor* of {`BestEffort`, `Batch`} (no `Yielding`).
   Per-profile is deferred to its own ADR (needs an authenticated principal + its own privacy review).
3. **New (from the panel): is layer-2 session isolation implementable on the Hermes path at all?** The
   spike answers it. If Hermes surfaces all thread-children as ONE MCP session, the fix is upstream
   Hermes (per-child connection), and the Hermes act path stays gated while Claude-Code ships. →
   wayland-computeruse-reviewer owns the spike.
