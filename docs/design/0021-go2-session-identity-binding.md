# GO-2 spike — binding act tokens to an MCP session identity (ADR-0021 open-Q1)

Status: **design spike — REVISED 2026-06-21** (ratification panel refuted the original "bus-name binding
is sufficient" resolution; replaced with a three-layer identity model + a decisive spike. No code here —
this stays design-only; the agentosd act-phase work is gated on the spike + ADR-0021 §Ratification-pass
must-fixes).
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

## Resolution (REVISED 2026-06-21 — the original "bus-name is enough" premise was refuted)

> **⚠ Superseded premise.** This doc originally resolved GO-2 onto a single invariant — "one MCP-server
> D-Bus connection per agent session" — and dismissed the multiplexed-Hermes case as "a caller that
> doesn't exist yet." The 2026-06-21 ratification panel **refuted that**: Hermes (installed + enabled on
> this box) runs ONE shared `agentosd mcp` connection and fans sub-agents out as in-process
> `ThreadPoolExecutor` threads, so N sub-agents share one bus name and bus-name binding cannot isolate
> them. Evidence: `~/.hermes/config.yaml:627` (`agentos` enabled) + `:404` (`inherit_mcp_toolsets`),
> `~/.hermes/hermes-agent/tools/mcp_tool.py:2076` (module-level `_servers`, one child per server name),
> `~/.hermes/hermes-agent/tools/delegate_tool.py:28,59` (children are threads, not subprocesses). The
> revised resolution below is the authoritative one; see ADR-0021 §Ratification pass.

**A three-layer identity model — each layer guards the grain it can actually see.**

1. **Daemon (`holder_peer`, B4 — merged `8c36c26`).** Binds a token to the D-Bus *connection* (unique
   bus name). `may_release` (lease.rs:463) refuses a foreign-token release; `do_acquire` binds the
   acquiring bus name (lease.rs:795); the `Release` verb passes `hdr.sender()` (lease.rs:1047). This is
   correct and kept — it stops a *different* bus client releasing the MCP server's token. It composes
   with B4 peer-disconnect auto-release (whole-process death frees the lease).
2. **MCP server (NEW — the fix for the multiplexed case).** The `agentosd mcp` server enforces
   *per-session* ownership **in-process**: at most one lease token per MCP session, and it refuses any
   `gpu_release` whose token that session did not acquire, *before* the D-Bus `Release` fires. The
   per-session key is ephemeral/in-memory/never-persisted/never-on-the-wire (no new identifiable state;
   not self-assertable by a sibling the way a protocol-carried `session_id` would be).
3. **Deployment.** Prefer connection-per-agent where the transport allows. The **Claude-Code stdio**
   path gets it for free (subprocess-per-session → distinct bus name); the **Hermes** path does not and
   needs layer 2.

## The decisive spike (the closing precondition — was wrongly deferred)
Layer 2 is only implementable if `agentosd mcp` can *distinguish* Hermes' in-process thread-children as
separate MCP sessions over the one stdio pipe. **If Hermes surfaces them as ONE `ClientSession`, the
server is blind too** and the only fix is upstream Hermes (per-child connection/process). Run it
(≤ half a day): under the installed Hermes (`agentos` enabled, `inherit_mcp_toolsets: true`), have a
parent delegate two concurrent children that each call an `agentos` tool; instrument the server to log,
per inbound `tools/call`, whether parent + the two children arrive as distinct MCP sessions or one.
- **Distinct sessions →** layer 2 is implementable; ship it.
- **One session →** Hermes act path stays **gated**; ship the Claude-Code path only until upstream
  Hermes gives each child its own connection.

### Spike result (RUN 2026-06-21) — ONE session: **both layers blind on the Hermes path**
Answered by architecture + a direct probe (`spikes/mcp-session-granularity/probe.sh`), no live LLM
delegation needed (it would only re-confirm what the transport dictates, at real cost to the live box):
- **Transport:** MCP stdio is one bidirectional stream = one session per `initialize`. The probe drove
  the real `agentosd mcp` with two `tools/call` frames on one stdin; the only per-frame discriminator
  is the JSON-RPC `id` — `mcp.rs` reads `id` + `method` + `params`, **no caller/session/principal field**.
- **Hermes client:** parent + all thread-children share ONE `ClientSession` — module-level `_servers`
  (`mcp_tool.py:2076`), one `MCPServerTask.session` / one `initialize` (`:1504`), tool calls via
  `server.session.call_tool` (`:2773`); children **inherit** the toolset, no new session
  (`delegate_tool.py:991`).

**Conclusion:** the server cannot tell Hermes' children apart, so **layer 2 is blind too** — it does
*not* rescue the multiplexed Hermes case as currently built. GO-2 for the Hermes act path therefore
needs an **upstream Hermes change**: either (a-Hermes) key `_servers` per child + spawn a per-child
`agentosd mcp` subprocess (→ distinct bus name → layer 1 suffices), or inject a *trusted* per-child
principal out-of-band that the server keys on (caller-supplied is rejected — self-assertable). **Until
then, ship the act verbs Claude-Code-stdio-scoped (subprocess-per-session → layer 1 suffices) and keep
the Hermes path GATED.** Layer 2's in-process per-session table is still worth building for the day
Hermes surfaces distinct sessions, but it is not the as-built Hermes fix.

The carried-`session_id` option (bind `(bus_name, session_id)` in the verb) is **rejected** — a sibling
behind a shared connection can supply another sibling's id (self-asserted identity behind a shared trust
boundary, the antipattern `AcquireAgent`-by-verb already avoids). Layer 2 in-process enforcement is the
substitute.

## Recommendation
Ship the act verbs **scoped to the connection-per-session transport (Claude-Code stdio)** with the
three-layer model; **gate the Hermes path** behind the spike result. Pin GO-2 with **two** tests, not
one: the merged D-Bus-grain foreign-token-release test (distinct bus names) **and** a new MCP-server-grain
test (two sessions, one connection → cross-session `gpu_release` refused) — the latter is the executable
proof that GO-2 is actually closed for the multiplexed case. Do not describe the act surface as
"GO-2-safe by construction": that sentence is false for the installed Hermes.

## Pinned tests (for whoever implements GO-2 in lease.rs)
- foreign-token release: a release from bus name B against a token held by bus name A → `false`, holder
  unchanged (identical to unknown-token).
- own-token release: release from the acquiring bus name → `true`, holder cleared.
- monotonic-token guard still holds (a stale token from the same name after preemption → `false`).
- outcome words (ADR-0021): `granted` / `busy_retry` (never `queued` — no wait-queue) / `denied`.
