# Act-verbs implementation spec (ADR-0020 §1 "act", gated by ADR-0021)

Status: **implementation-ready design** (no code — `mcp.rs`/`lease.rs` are the parallel session's
active territory). Closes the one unresolved gap before the act verbs can be built: **how a caller is
classed `Agent` at the D-Bus boundary.** GO-1 (`fbcc6a8`) and GO-2 (`8c36c26`) are both merged.

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
  to `Batch`, can't preempt; release identity-bound). The trust boundary for *who may talk to the bus
  at all* is ADR-0013 A1 (private socket + SO_PEERCRED), a separate, deferred hardening — orthogonal to
  this, because no caller of `AcquireAgent` can do harm a Trusted caller couldn't.
- Cooperative only (`argv = None`): agents never get owned `Spawn` (ADR-0020 — allowlisted profiles,
  no agent binary path). `holder_peer` binds the token to the MCP server's connection (GO-2).

## The MCP side (`mcp.rs`)
The perceive server is sync (blocking stdio loop) with **no D-Bus connection**. The act tools add one:
- A process-lifetime zbus session-bus connection (one per `agentosd mcp` process = one session — the
  GO-2 spike invariant). Drive it from the sync handlers via a small `tokio` runtime `block_on`, or a
  dedicated blocking proxy. zbus is already a dependency.
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

## Safety invariants (all already enforced by the merged core — this just wires to them)
1. Tier clamp: `AcquireAgent` → `CallerClass::Agent` → `clamp(Batch)` before `arbitrate` (GO-1). An
   agent can never preempt the desktop. Pinned by the existing GO-1 test + a new end-to-end one.
2. Release identity: `holder_peer` binds the token to the MCP connection (GO-2). Foreign release fails.
3. No raw `Spawn`, no SIGKILL/preempt verb exposed (ADR-0020). Eviction stays code-driven.
4. Admission is the unchanged scalar predict-before-load core; the agent gets the real verdict, never a
   bypass.

## Test plan
- Pure/core: `AcquireAgent` path installs `Batch` for an `interactive` request; queues (→ `busy_retry`)
  behind a live `Interactive`, never preempts. (Extends the GO-1 test through the new method.)
- D-Bus integration (busctl, like the existing lease smoke tests): `AcquireAgent interactive` → granted
  as batch; a second connection's `Release` of that token → refused (GO-2); same connection → released.
- MCP: `tools/list` now has `gpu_request`/`gpu_release` with schemas; `gpu_request` returns
  granted/busy_retry/denied shapes; `gpu_release` of a foreign/stale token → `not_holder`.

## Review hooks (ADR-0021 deciders — do BEFORE merge, not after)
`determinism-safety-reviewer` (the clamp is the only thing between an agent and a desktop preempt —
verify it's on every agent path), `resource-safety-reviewer` (admission verdict honesty, no bypass),
`wayland-computeruse-reviewer` + `responsible-ai-privacy-skeptic` (the new act surface + identity
scoping). This is a new privileged-ish surface; it warrants the panel ADR-0020/0021 reserved for it.

## Sequencing note
Buildable now (both gates merged), but it touches `lease.rs` (new D-Bus method) and `mcp.rs` — the
parallel session's files. Build in an isolated worktree or after confirming that session is clear of
the core, then run the review hooks before merging.
