# Ungating the Hermes act path — decision memo (ADR-0021 GO-2, the last open leg)

Status: **decision-ready scoping (2026-06-21) — DECISION IS COREY'S.** No code here and none proposed in
`~/.hermes` (a vendored upstream, Nous Research / MIT — CLAUDE.md: don't reinvent the orchestrator). This
memo grounds the three real options in the *actual* Hermes MCP code and recommends a path, so the human can
decide whether/how to ungate. The agentosd side is already built (ADR-0021, commit `c3fdd3b`, deployed
live): `AcquireAgent` + the MCP act verbs + the layer-2 `SessionTable` are in place and need **no further
change** for any option below.

## The gate, in one paragraph

GO-2 stops one agent releasing/renewing another's GPU lease (a cross-agent DoS). It is **closed for the
Claude-Code stdio path** (subprocess-per-session → distinct D-Bus bus name → the daemon's layer-1
`holder_peer` binding isolates each session for free). It is **GATED for Hermes** because the installed
Hermes multiplexes a parent + up to `max_concurrent_children: 3` in-process `ThreadPoolExecutor` threads
over **one** shared `agentosd mcp` connection, and the MCP frame carries no caller identity — so neither
the daemon (one bus name) nor the MCP server (one session, no per-call principal) can tell the children
apart. Verified by `spikes/mcp-session-granularity/probe.sh` (ADR-0021 §Ratification → Spike RESULT).

## The constraint that decides everything (grounded in the code)

- `~/.hermes/hermes-agent/tools/mcp_tool.py:2076` — `_servers: Dict[str, MCPServerTask]` is **module-level,
  keyed by server NAME**: exactly one `agentos` entry per Hermes process.
- `:1458-1501` — that entry is ONE `StdioServerParameters`/`stdio_client`/`ClientSession`, one spawned
  subprocess, one persistent connection.
- `:2773` — every tool call, parent or child, runs `server.session.call_tool(tool_name, arguments=args)`
  on that single shared session (serialized by `server._rpc_lock`).
- `delegate_tool.py` — delegated sub-agents are in-process threads that **inherit** the toolset
  (`inherit_mcp_toolsets: true`, config.yaml:404); they do not get their own connection or principal.

⇒ The only per-call field is `arguments` (caller-supplied, therefore **spoofable** by a sibling). A
"session token in the args" is rejected for exactly this reason (ADR-0021 / design 0021): self-asserted
identity behind a shared trust boundary. So a *trusted* per-child identity must come from a place a sibling
cannot forge — which, given the architecture, means **a per-child connection** or **a per-child toolset
policy**, not a per-call field.

## The three options

### Option A — per-child MCP connection (full fidelity; heavy)
Spawn a **separate `agentos` connection per delegated child** (key `_servers` by `(name, child_id)`, manage
its lifecycle, tear it down when the child thread ends). Each `agentosd mcp` subprocess opens its own
session-bus connection → distinct bus name → the daemon's **layer-1 `holder_peer` already isolates them**,
and a child's connection drop fires B4 auto-release. **agentosd change: none.**
- **Blast radius: HIGH (Hermes core).** Touches the shared `_servers` registry, the per-server circuit
  breaker (`:2076`+), the env-filtering + PID/pgid cleanup (`:1482`), and the `_rpc_lock` model — all of
  which are per-server-NAME today. Multiplies resources: up to 4 concurrent `agentosd mcp` subprocesses +
  4 D-Bus connections per Hermes process (parent + 3 children).
- **Pro:** the cleanest identity story, reuses the proven layer-1 mechanism, isolates siblings completely.
- **Con:** major surgery on a hot, security-sensitive shared path in *someone else's* codebase; resource
  multiplication; you maintain a fork or upstream it to Nous Research.

### Option B — trusted per-call principal (DOMINATED / not feasible as-is)
Keep one shared connection; have Hermes stamp each `tools/call` with a per-child principal the agentosd
server keys its layer-2 `SessionTable` on. **Blocked:** MCP `call_tool` has no principal field, and the
only carrier (`arguments`) is sibling-spoofable. Making it trusted needs either a per-connection split
(= Option A), an MCP **protocol** extension for caller identity (upstream MCP spec, not just Hermes), or a
fragile out-of-band side-channel (Hermes writes a per-child token to a file agentosd cross-checks — new
racy state, rejected on the same "no new identifiable state / no self-assert" grounds). **Recommend:
reject.** It is strictly dominated by A.

### Option C — parent-only act toolset (recommended near-term; light)
Give the `agentos` **act** verbs (`gpu_request`/`gpu_release`) to the **top-level Hermes agent only**, and
**deny them to delegated sub-agents** (children stay perceive-only, or without the `agentos` server). Then
there is exactly **one act principal per Hermes process** — no sibling to DoS — so the shared connection's
single bus name maps correctly to that one principal, and the daemon's layer-1 binding is sound by
construction. A child cannot call `gpu_release` it doesn't have (Hermes routes only an agent's own
toolset), so the shared transport is irrelevant.
- **Blast radius: LOW.** A toolset-scoping policy: Hermes already controls inheritance
  (`inherit_mcp_toolsets`, config.yaml:404) — scope the `agentos` act tools to the parent (a per-server /
  per-tool inheritance rule, config or a small `delegate_tool` change). **agentosd change: none.**
- **Pro:** ungates Hermes act safely with minimal upstream change, and it matches the natural model — **one
  Hermes job = one GPU lease, held by the parent** (sub-agents doing *independent* concurrent GPU
  acquisitions is the unusual case, and is exactly the case that needs A).
- **Con:** a sub-agent can't independently hold its own lease; it coordinates GPU work through the parent.

## Recommendation

1. **Do NOT ungate speculatively.** The as-built posture (Claude-Code act enabled; Hermes gated) is correct
   and safe today. Ungating is **triggered by a real need** — a Hermes workflow that must call the act
   verbs. Until then, leave it gated; this memo is the playbook for when the need lands.
2. **When the need lands, start with Option C** (parent-only act toolset). It is the smallest change, it is
   sound by construction, and it fits the one-job-one-lease model. The layer-2 `SessionTable` is already
   built and will simply hold the parent's single token.
3. **Escalate to Option A only if** sub-agents genuinely must acquire **independent, concurrent** GPU leases
   (not coordinate through the parent). Prefer upstreaming the per-child-connection change to Nous Research
   over a local fork — it benefits any MCP server that needs per-child identity, and it keeps `~/.hermes`
   un-forked.
4. **Reject Option B.**

## What this does NOT require
- No agentosd change for A or C — the daemon's layer-1 binding + the MCP layer-2 table already cover both.
  (B would have needed a new trust mechanism, another reason to reject it.)
- No change to the Claude-Code path, which is already closed and live.

## Decision owner
Corey. It is a change to (or a policy on) the `~/.hermes` upstream and a product call about whether Hermes
sub-agents need independent GPU leases. Record the choice as an amendment to ADR-0021 §Open-Q (the Hermes
leg) when made.
