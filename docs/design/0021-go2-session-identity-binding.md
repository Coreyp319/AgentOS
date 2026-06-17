# GO-2 spike — binding act tokens to an MCP session identity (ADR-0021 open-Q1)

Status: **design spike** (resolves the one open question gating ADR-0021 GO-2; no code here — the
agentosd core is being edited concurrently, so this stays design-only).
Resolves: ADR-0021 §GO-2 + Open-Q1 — "does the MCP server get a distinct bus name per agent session,
or must it carry a session token through `gpu_request`/`gpu_release` to attribute a token to a
session behind a shared connection?"

## The question, concretely
GO-2 requires that `gpu_release(token)` only succeed for the identity that acquired the token, so one
agent can't release (DoS) another's lease. The existing mechanism is `holder_peer` (ADR-0013 B4),
which binds a token to the **D-Bus unique connection name**. The open question is whether that
granularity is fine, given the Claude-Code ↔ Hermes ↔ agentosd-MCP hop where *many* agents might sit
behind *one* connection.

## What the transport actually is
- `agentosd mcp` is a **stdio subprocess spawned by the MCP client** (Claude Code launches one per
  session via `claude mcp add … -- agentosd mcp`). So for the Claude-Code path there is **one MCP
  server process per session**, with its own lifetime.
- The MCP server (perceive-v1) holds **no D-Bus connection** today. The act verbs will need one: the
  server calls `Coordinator1.Acquire/Release`. If each `agentosd mcp` process opens its **own** D-Bus
  connection, it gets its **own unique bus name** → `holder_peer` already attributes tokens to *that
  process* = *that session*.

## Resolution
**Bus-name binding (holder_peer, B4) is sufficient for v1 — on one invariant: one MCP-server D-Bus
connection per agent session.** This holds for the Claude-Code stdio transport by construction
(subprocess-per-session). Concretely:

1. The act-phase MCP server opens **one D-Bus connection per process** and acquires/releases over it.
   `holder_peer` then binds each act token to that connection's unique name.
2. `release_token` (lease.rs:352) gains an identity check: release succeeds only if the caller's bus
   name equals the token's `holder_peer` name — foreign-token release returns the **same failure as an
   unknown token**, no state change (the ADR-0021 GO-2 pinned test).
3. This composes with B4's existing peer-disconnect auto-release: if the MCP session dies, its lease
   frees automatically — same mechanism, now also the release-authz boundary.

## The edge case it does NOT cover (and the deferral)
A transport that **multiplexes many agents over one long-lived connection** — plausibly a future
**Hermes** that proxies N agents through a single shared `agentosd mcp`/D-Bus connection — defeats
bus-name granularity (all N share one `holder_peer` name → agent A could release agent B's token).

Two clean options, both deferred behind the v1 invariant:
- **(a) Connection-per-agent (preferred, no protocol change).** Require the multiplexer to open one
  agentosd connection per agent session. Keeps the core unchanged; pushes identity to the transport
  boundary where it belongs. Hermes already spawns per-agent contexts, so this is natural.
- **(b) Session-token in the verb (fallback, protocol change).** Carry an opaque `session_id` through
  `gpu_request`/`gpu_release`; bind tokens to `(bus_name, session_id)`. Only needed if a multiplexer
  genuinely cannot do (a). Strictly more surface — adopt only if forced.

## Recommendation
Ship GO-2 v1 on **bus-name binding + the one-connection-per-session invariant**, documented as a
**precondition on any act-phase MCP client** (Claude-Code satisfies it for free). Add the foreign-token
release test. Treat the multiplexed-Hermes case as a **separate, later** decision (option (a) first),
NOT a v1 blocker. This unblocks GO-2 without the open question stalling it, and without widening the
verb protocol for a caller that doesn't exist yet.

## Pinned tests (for whoever implements GO-2 in lease.rs)
- foreign-token release: a release from bus name B against a token held by bus name A → `false`, holder
  unchanged (identical to unknown-token).
- own-token release: release from the acquiring bus name → `true`, holder cleared.
- monotonic-token guard still holds (a stale token from the same name after preemption → `false`).
- outcome words (ADR-0021): `granted` / `busy_retry` (never `queued` — no wait-queue) / `denied`.
