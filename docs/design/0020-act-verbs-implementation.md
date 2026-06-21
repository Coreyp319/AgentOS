# Act-verbs implementation spec (ADR-0020 §1 "act", gated by ADR-0021)

Status: **BUILT (2026-06-21) — Claude-Code-stdio-scoped; Hermes path GATED.** The
`AcquireAgent`-class-by-verb design below is implemented, with all ten ADR-0021 ratification must-fixes
folded in (typed outcome codes, ~90s agent TTL + a TTL-derived `Renew` heartbeat, random agent tokens,
the MCP `SessionTable` layer-2 guard, fail-closed act, `holder_peer` no-leak, the agent floor). GO-2's
*session-grain* isolation is enforced in-process (layer 2) but, per the spike, cannot see Hermes'
shared-connection thread-children — so act is enabled for the Claude-Code stdio transport (one bus name =
layer-1 suffices) and the Hermes path is gated on an upstream Hermes change. Verified: 162 tests, clippy
clean both ways, live isolated-bus smoke, 5-lens adversarial review (findings fixed). See ADR-0021
§Ratification → Implementation status for the per-item map.

## The gap GO-1/GO-2 left open
- GO-1 put the tier clamp in `do_acquire(caller, class, …)`, applied before `arbitrate`. **But the
  D-Bus `Acquire`/`Spawn` methods both hardcode `CallerClass::Trusted`** (lease.rs ~659/679). So today
  the clamp never fires — there is *no way to acquire as `Agent`*.
- GO-2 binds *release* identity (`holder_peer`). It does not provide an *acquisition* class path.
- So the act verbs need a third, small piece: a D-Bus entry the daemon classes as `Agent`.

## Decision — a distinct `AcquireAgent` D-Bus method (not a spoofable param)
Add one method to `org.agentos.Coordinator1`:

```
AcquireAgent(tier: String, estimate_mib: u32) -> (granted: bool, token: u64, outcome: String)
  → do_acquire(caller = hdr.sender(), CallerClass::Agent, tier, estimate_mib, argv = None)
```

- **Why a separate method, not `caller_class` on `Acquire`:** a parameter is self-asserted — a client
  could pass `Trusted`. A separate method makes the class a property of *which verb you called*, which
  the daemon controls. Calling `AcquireAgent` only ever yields `Agent` (clamped + identity-bound);
  there is no way to escalate. `Acquire` stays the `Trusted` Hermes/human/CLI path.
- **Is "anyone can call `AcquireAgent`" a hole?** No — `Agent` is strictly *more* restrictive (clamped
  to `Batch`, can't preempt; release identity-bound), so `AcquireAgent` itself adds no harm a Trusted
  caller couldn't already do. **But the panel corrected the framing (ADR-0021 §Ratification item 9):**
  ADR-0013 A1 (private socket + `SO_PEERCRED`) is **not "orthogonal" — it is the deferred *closer***. The
  `Trusted` verbs (`Acquire`/`Spawn`/`AdoptScope`) remain a same-uid desktop-DoS path that the act
  surface sits beside; A1, or the weaker per-call `GetConnectionUnixUser == geteuid()` stopgap, is what
  bounds it. "AcquireAgent is more restrictive" is true but is *not* the whole risk story.
- Cooperative only (`argv = None`): agents never get owned `Spawn` (ADR-0020 — allowlisted profiles,
  no agent binary path). `holder_peer` binds the token to the MCP server's connection (GO-2).

## The MCP side (`mcp.rs`)

> **⚠ Ratification correction (2026-06-21).** This section assumed "one `agentosd mcp` process = one
> session = one bus name (the GO-2 spike invariant)." That is **false for the installed Hermes path**
> (one shared `agentosd mcp` connection, in-process thread-children → many sessions, one bus name). GO-2
> identity is now a three-layer model and the MCP server must enforce *per-session* ownership in-process
> (refuse a `gpu_release` of a token a different session acquired, before issuing `Release`) — gated on
> a spike that confirms the server can even see the children as distinct sessions. See
> `docs/design/0021-go2-session-identity-binding.md` §Resolution + ADR-0021 §Ratification pass. The
> act verbs ship **Claude-Code-stdio-scoped** until the spike clears the Hermes path.

The perceive server is sync (blocking stdio loop) with **no D-Bus connection**. The act tools add one:
- A process-lifetime zbus session-bus connection. Drive it from the sync handlers via a small `tokio`
  runtime `block_on`, or a dedicated blocking proxy. zbus is already a dependency. `holder_peer` binds
  the token to *this connection* (the daemon grain — layer 1); the server additionally tracks which MCP
  *session* minted each token and refuses cross-session release (layer 2). **The `block_on` carries a
  short timeout and fails CLOSED** (coordinator down ⇒ `{status:"unavailable"}`, no grant, the agent must
  not launch heavy GPU work — the inverse of Hermes interactive's fail-open; ADR-0021 §Ratification
  item 4).
- **`gpu_request(tier, estimate_mib)`** → `AcquireAgent` → returns the *real* admit/arbitrate verdict:
  - `{status: "granted", token, tier_effective}` — note `tier_effective` shows the clamp (an agent
    asking `interactive` gets `batch`; surface it honestly, don't hide the clamp).
  - `{status: "busy_retry", reason}` — lease held by an equal/higher tier. **NOT "queued"** — `lease.rs`
    has no wait-queue; the caller must retry (ADR-0021 outcome words).
  - `{status: "denied", short_mib}` — predict-before-load said it won't fit.
- **`gpu_release(token)`** → `Release` over the SAME connection → GO-2 lets only this server release it.
  `{status: "released"}` | `{status: "not_holder"}`.
- The MCP server holds the token between calls (per session). `tools/list` must now include these two;
  the perceive contract test that asserts their ABSENCE flips to assert their presence + schema.

## Safety invariants (mostly enforced by the merged core; items 2 + 5–6 are the ratification must-fixes)
1. Tier clamp: `AcquireAgent` → `CallerClass::Agent` → `clamp(Batch)` before `arbitrate` (GO-1). An
   agent can never preempt the desktop. Pinned by the existing GO-1 test + a new end-to-end one. *Add an
   agent **floor** too: agent class ∈ {BestEffort, Batch} — never `Yielding`.*
2. Release identity: `holder_peer` binds the token to the MCP *connection* (GO-2, daemon grain). Foreign
   *cross-connection* release fails. **⚠ NOT sufficient alone** — same-connection sibling release is
   open (Hermes); the MCP server must isolate per-session (layer 2). And **`Renew` is currently
   unguarded** (`lease.rs:1054` — token-only) — add `may_renew` mirroring `may_release`.
3. No raw `Spawn`, no SIGKILL/preempt verb exposed (ADR-0020). Eviction stays code-driven.
4. Admission is the unchanged scalar predict-before-load core; the agent gets the real verdict, never a
   bypass. **Honesty residual:** a cooperative agent lease is not VRAM-reclaimable on preempt —
   Interactive wins the lease but may still CPU-offload (state it; CONCUR/Phase-3 is the deferred fix).
5. **Typed outcomes, not prose:** `do_acquire` returns free text today (incl. the word "queued",
   forbidden). The act verbs need a stable `outcome_code` + numeric `short_mib`/`retry_after_ms`; map the
   C7 cooldown to `busy_retry`+`retry_after`. (ADR-0021 §Ratification item 2.)
6. **Per-class agent TTL (~90 s) + Renew heartbeat** (not the 90-min dream default), and **unguessable
   (random) agent tokens** (not the sequential `next_token`). (ADR-0021 §Ratification items 3 + 5.)

## Test plan
- Pure/core: `AcquireAgent` path installs `Batch` for an `interactive` request; queues (→ `busy_retry`)
  behind a live `Interactive`, never preempts. (Extends the GO-1 test through the new method.)
- D-Bus integration (busctl, like the existing lease smoke tests): `AcquireAgent interactive` → granted
  as batch; a second connection's `Release` of that token → refused (GO-2 daemon grain); same connection
  → released.
- **Server-grain (the ratification must-have):** two MCP *sessions* over **one** connection → session B's
  `gpu_release` of session A's token → refused. The D-Bus test above passes while this hole is open
  (two siblings are one connection), so a green D-Bus bar must NOT be read as GO-2-closed.
- **`Renew` identity:** a foreign peer/session `Renew` of a held token → `false`, no deadline change
  (mirror the foreign-release test).
- **Fail-closed:** coordinator absent/timed-out → `gpu_request` returns `unavailable` promptly (bounded),
  never hangs, never a spurious `granted`.
- MCP: `tools/list` now has `gpu_request`/`gpu_release` with schemas; `gpu_request` returns
  granted/busy_retry/denied/unavailable shapes (typed codes, never the word "queued"); `gpu_release` of a
  foreign/stale token → `not_holder`.

## Review hooks (ADR-0021 deciders — do BEFORE merge, not after)
`determinism-safety-reviewer` (the clamp is the only thing between an agent and a desktop preempt —
verify it's on every agent path), `resource-safety-reviewer` (admission verdict honesty, no bypass),
`wayland-computeruse-reviewer` + `responsible-ai-privacy-skeptic` (the new act surface + identity
scoping). This is a new privileged-ish surface; it warrants the panel ADR-0020/0021 reserved for it.

## Sequencing note
**Not "buildable now" as originally written** — gated on the ADR-0021 §Ratification spike (can the MCP
server see Hermes' thread-children as distinct sessions?) + the must-fix list. Sequence: (1) run the
spike; (2) land the small core hardenings that are independent of the spike (`may_renew` identity,
typed `outcome_code`, per-class agent TTL, random agent tokens) — these are buildable now and several
are latent-defect fixes; (3) build `AcquireAgent` + the MCP act wiring with layer-2 session isolation,
Claude-Code-scoped, Hermes path gated on the spike result. It touches `lease.rs` + `mcp.rs` (the
parallel session's files) — build in an isolated worktree or after that session is clear of the core,
then run the review hooks before merging.

## §#10 — per-caller `gpu_why` correlation id (DONE 2026-06-21)

The last open ADR-0021 item. Lets an agent ask "why did **my** request wait?" and get back ONLY its own
lost contentions, **never naming another holder** (ADR-0021 must-fix #10 — an identity-leak guardrail).

**Decision — MCP-layer synthesis (not daemon-authoritative).** Two designs were live:

- *(B, chosen)* Synthesize the per-caller narration in `mcp.rs` from the daemon's agent reply tuple
  `(granted, token, code, tier_effective, short_mib, retry_after_ms)` — which carries **no holder name**
  (the holder-naming `msg` goes only to the *trusted* busctl/log path) — plus the heartbeat's own
  lease-lost observation. Mint the correlation id in the act layer.
- *(A, rejected)* Have `AcquireAgent` return a daemon-minted id, the daemon keep a per-caller note (incl.
  an authoritative "you were preempted"), and a new `WhyAgent` query verb scope to the caller's bus name.

B was chosen because it is smaller (one file, no new D-Bus verb, no new daemon state), touches nothing
safety-critical, and — decisively — makes "**never name another holder**" a **structural** property: the
act layer literally never *receives* a holder name, so it cannot leak one. A makes it a runtime filter on
a daemon that *does* know holder names — the exact leak surface #10 warns about. This is a documented
deviation from the ADR's literal "`AcquireAgent` returns a correlation id": the id is minted in the act
layer, honoring the intent (every `gpu_request` reply carries a `request_id`) without the daemon surface.

**Shape.**
- `gpu_request` mints a monotonic `request_id` (returned on **every** reply — granted/busy_retry/denied/
  unavailable). On a *loss* it records a holder-free `Contention{request_id, kind, why}` in a bounded
  per-session ring (cap 8). A grant records nothing; `unavailable`/`error` are substrate blindness, not a
  lease wait.
- The `Renew` heartbeat, on a `Renew → false` for a token we *still believed we held* (i.e. lost out from
  under us, not self-released), records a `lease_lost` contention with a fresh id. It is **honest-ambiguous**
  ("preempted by a higher-priority request, or it expired") — the act layer can't tell preempt from
  TTL-expiry without daemon help.
- `gpu_why(request_id?)`: **focused** (id given) answers purely from the caller's own ring — never the
  system `last_contention` (which *may* name a holder), and works even when blind; **broad** (no id) keeps
  the existing system card and adds `your_recent_contention[]` (holder-free, most-recent first).

**State.** Process-lifetime, in-memory, per-session-keyed (mirrors `SessionTable`), never persisted — dies
with the `agentosd mcp` process (same privacy bar as ADR-0021 #7). On the live Claude-Code stdio path one
process = one session = `LOCAL_SESSION`, so the per-session ring is this agent's ring; lookups are scoped
to the caller's own ring, so a guessed id from another session simply isn't found.

**Deferred (daemon-authoritative refinement).** A future `WhyAgent`-style path could narrate preempt vs
expiry *authoritatively* (and reuse the original acquire's id on an async preempt). Only worth it if the
honest-ambiguous loss line proves insufficient in practice; it reintroduces the daemon-side leak surface,
so it would need the privacy/resource panel.

**Tests (`mcp.rs`):** `contention_why_is_holder_free_and_only_for_real_waits`,
`lease_lost_narration_is_ambiguous_and_holder_free`,
`contention_log_ring_is_bounded_per_session_and_ids_are_monotonic`,
`why_focused_query_is_caller_only_and_never_the_system_card` (the no-leak pin — a holder name in the
keyhole cannot appear in the focused reply), `why_focused_query_for_unknown_id_is_an_honest_no_record_even_when_blind`,
`why_broad_query_surfaces_caller_contention_holder_free_alongside_the_system_card`,
`why_broad_query_is_not_blind_when_only_caller_history_exists`,
`gpu_why_schema_advertises_an_optional_request_id`.
