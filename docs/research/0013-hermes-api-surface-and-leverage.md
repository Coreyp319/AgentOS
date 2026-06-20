# Research 0013 — Hermes API surface & how well AgentOS leverages it

- Date: 2026-06-19
- Method: read-only inspection of the live `~/.hermes` install + the AgentOS code/ADRs
- Relates to: ADR-0001 (substrate-not-orchestrator), ADR-0002 (proxy), ADR-0006/0010
  (lease + Hermes plugin), ADR-0011 (overnight pipeline), ADR-0012 (keyhole),
  ADR-0019 (reviewable queue), ADR-0020 (agent-facing MCP)

## TL;DR

AgentOS honors the "don't reinvent — Hermes is the orchestrator" thesis with unusual
discipline, but the *integration* is **correct, shallow, and one-directional**. We read
Hermes state and we serialize the GPU for it; we almost never write back, and the richest
closed-loop features were all parked behind a Hermes write-API we hadn't confirmed. **This
inspection confirms that write-API exists** — so the recurring blocker is downgraded from a
question to a deploy/build task.

Grade: **B / B+.** Reuse discipline A−; read path B; control plane B+; write-back D;
deeper-capability reuse C.

## What is actually running in `~/.hermes`

- **Gateway** (`hermes`, pid on `127.0.0.1:9119`) — `gateway_state.json` = `running`.
- **Ollama** on `127.0.0.1:11434`; **ComfyUI** on `:8188`.
- **Inference points straight at Ollama**: `config.yaml → base_url: http://localhost:11434/v1`.
  Nothing on `:11435`; **no agentosd proxy in the path**.
- **Installed plugins**: only `needs-you-signal`. **`gpu-coordinator` is NOT installed live.**
- Cron engine, skills dir (~30 skills), memories, kanban.db (WAL) all present.

## The Hermes REST control API (the resolved write-API gate)

`gateway/platforms/api_server.py` — coded, documented, but **not currently bound**
(default port **8642** / `API_SERVER_PORT`; `platforms.api_server` not enabled in config):

| Endpoint | Verb | AgentOS use |
|---|---|---|
| `/v1/runs/{id}/approval` | POST | keyhole **approve / steer** (ADR-0012 P2) |
| `/v1/runs/{id}/stop` | POST | keyhole **pause / cancel** (ADR-0012 P2) |
| `/v1/runs` | POST | create work (ADR-0019 Phase-3 Hermes-mirror) |
| `/v1/runs/{id}/events` | GET (SSE) | replace the brittle kanban.db SQL poll |
| `/health/detailed` | GET | real liveness — fixes "stale file vs live" blind spot |
| `/v1/capabilities` | GET | machine-readable feature discovery |
| `/v1/skills`, `/v1/toolsets`, `/api/sessions*` | GET/POST | session + skill surface |

Plus the CLI write path `hermes_cli/kanban_db.py: create_task(...)`, and **routines ship
today**: `hermes cron create`, `hermes webhook subscribe`, API triggers
(`hermes-already-has-routines.md`) — ADR-0011's overnight pipeline should ride these verbatim.

## Leverage scorecard

| Dimension | Grade | Evidence |
|---|---|---|
| Reuse discipline (don't-reinvent) | A− | every ADR carries a "Hermes is the scheduler, stop and escalate" clause; keyhole board = link-out to `:9119`; ADR-0019 has a `SystemExit` tripwire so its buffer can't become a scheduler |
| Read path (consume Hermes state) | B | `feed.rs`/`keyhole.rs` fail-open reads of `kanban.db` + `gateway_state.json` + `needs_you.json`; **brittle** — raw SQL into Hermes' internal `tasks` table, no schema pin (now probed loudly at startup, this change) |
| Control plane (Hermes → agentosd) | B+ | `gpu-coordinator` plugin wraps every inference in `Acquire`/`Release` (built, tested); preempt SIGKILLs batch — **but the plugin is not installed live, so it's test-proven, not production-active** |
| Write-back (agentosd → Hermes) | D | only outbound write is `agent.json` for the wallpaper (not Hermes); keyhole can show fleet state but not steer it. The API above is the unlock |
| Deeper capabilities (cron/skills/memory/delegation) | C | declared ("ride Hermes' cron + kanban", pins in Hermes memory) but mostly unbuilt; ADR-0011 ran by hand |

## Gaps, in priority order

1. **The write-API exists — now enable + consume it.** Flip `platforms.api_server` on
   (binds `:8642`), then build the agentosd→Hermes client for the P2 steer verbs. Unblocks
   ADR-0012 (keyhole actions), ADR-0019 Phase-3, ADR-0020 act-verbs in one move.
2. **Install the `gpu-coordinator` plugin live.** It ships in
   `integrations/hermes/gpu-coordinator/` but `~/.hermes/plugins/` has only
   `needs-you-signal`. Until enabled, "Hermes inference preempts batch" is a test, not a
   running guarantee. This is a deploy step, not code.
3. **Read coupling hardened** (done in this pass): `feed.rs` now probes the `tasks` schema
   once at startup and logs `WARNING` on drift instead of sliding silently to idle. Next
   step if desired: migrate the poll to `GET /v1/runs/{id}/events` (SSE) once `:8642` is up,
   replacing raw SQL with a stable API boundary; use `/health/detailed` for liveness.
4. **Proxy decided** (ADR-0002, this pass): enforcing proxy DEFERRED — no consumer reads the
   priority header and the lease already covers the safety-critical path. Supersede if never
   needed.
5. **Watch for forward drift** (not present today): ADR-0020 Phase-2 CONCUR admission
   (must regulate count, never order) and ADR-0011's `design-council.js` nightly (must
   materialize kanban cards, not grow a private queue) are the two places a parallel
   scheduler could re-emerge. Both unbuilt; both name the boundary as the tripwire.

## Changes made alongside this note (2026-06-19)

- `crates/agentosd/src/feed.rs` — `probe_fleet_schema` + `SchemaCheck`; startup loud-warn on
  Hermes `tasks` schema drift; 3 tests (101 crate tests green, clippy clean).
- ADR-0002 — status → enforcing proxy **DEFERRED**; dated note (inference goes direct to Ollama).
- ADR-0006 / ADR-0010 — corrected the stale "plugin unbuilt" claim → **built, not installed live**.
- ADR-0012 — amendment: the Hermes write-API gate (item 3) **RESOLVED** (it exists; enable + build client).
