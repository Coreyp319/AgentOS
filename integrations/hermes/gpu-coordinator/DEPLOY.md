# gpu-coordinator — LIVE deploy procedure

Reproducible record of the live deployment performed **2026-06-19**: the
`gpu-coordinator` Hermes plugin was installed/enabled against the live
`~/.hermes` install, and the Hermes REST control API was bound. Relates to
ADR-0006 (Hermes plugin, not a fork), ADR-0010 (VRAM lease), ADR-0012 amendment
2026-06-19 (Hermes write-API gate RESOLVED), and `docs/research/0013-hermes-api-surface-and-leverage.md`.

## What this enables

The `gpu-coordinator` plugin registers ONE `llm_execution` middleware that wraps
**every Hermes inference** in the agentosd **interactive VRAM lease** —
`Acquire`/`Release` on `org.agentos.Coordinator1` (ADR-0006/0010). While a live
turn holds the interactive lease, any running overnight dream / batch job
(ComfyUI) is preempted (SIGKILL'd by the `lease` daemon) and the lane reopens
when the last concurrent inference finishes. The hold is refcounted process-wide
(concurrent inferences share one lease, never serialized) and fail-open: a
down/slow/denying coordinator just means inference proceeds un-leased.

It uses a **persistent jeepney D-Bus connection** (`lease_client.py`
`JeepneyLeaseClient`). This is **required for correctness, not a preference**: the
daemon peer-binds a cooperative `Acquire` to the caller's D-Bus connection and
auto-releases the lease when that connection drops (ADR-0013 B4). A long-lived
connection means the cooperative lease survives across calls and B4 only fires
when the plugin process actually dies. The older ephemeral `busctl` transport
opens a fresh connection per call that dies immediately, so the daemon
auto-releases seconds later — `renew_failed` churn, the hold never persists, the
keyhole never shows it. jeepney is therefore the headline fix this deploy
verified.

## Prerequisites

- The `agentosd lease` daemon must be running (serves `org.agentos.Coordinator1`).
  Verify:

  ```bash
  busctl --user list | grep Coordinator
  ```

- **jeepney** must be importable in the Hermes venv (pure-python, no native
  deps). The venv has no `pip`, so install with `uv`:

  ```bash
  uv pip install --python /home/corey/.hermes/hermes-agent/venv/bin/python jeepney
  ```

  If jeepney is absent the plugin **falls back to the `busctl` transport** — still
  fully functional and fail-open, but it churns the cooperative lease (the hold
  never persists; see `lease_client.py`). Install jeepney for the persistent path.

## Install the plugin

Copy the lean fileset into the live Hermes plugins dir (mirroring the existing
`needs-you-signal` plugin layout):

```bash
mkdir -p ~/.hermes/plugins/gpu-coordinator
cp integrations/hermes/gpu-coordinator/{__init__.py,coordinator.py,lease_client.py,lease_state.py,plugin.yaml} \
   ~/.hermes/plugins/gpu-coordinator/
```

Then enable it by adding `gpu-coordinator` to `plugins.enabled` in
`~/.hermes/config.yaml`.

## (Optional) enable the Hermes REST control API

The Hermes write/approval API (`gateway/platforms/api_server.py`) is coded but
**env-gated and not bound by default**. Binding it unblocks the ADR-0012 / 0019 /
0020 write-paths. To bind it on `127.0.0.1:8642` with Bearer auth, add to
`~/.hermes/.env`:

```bash
# generate a secret:
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

```dotenv
API_SERVER_ENABLED=true
API_SERVER_KEY=<the generated secret>
```

Endpoints this exposes (see research 0013):

- `POST /v1/runs/{id}/approval` — resolve a pending run approval (keyhole approve/steer)
- `POST /v1/runs/{id}/stop` — interrupt a running agent (keyhole pause/cancel)
- `POST /v1/runs` — start a run (202 + run_id)
- `GET /health/detailed` — rich liveness
- `GET /v1/capabilities` — machine-readable feature discovery

## (Optional) wire AgentOS introspection into Hermes (MCP)

Gives every Hermes session — including the Telegram bot on the go — three
**read-only** GPU-introspection tools from the `agentosd mcp` server (ADR-0020
Phase 1, JSON-RPC over stdio): `gpu_status` (free/used/total VRAM + lease tier +
resident models), `gpu_residency` (warm-pool/heavy-lane plan + each model's
learned admission footprint), `gpu_why` (plain-language last-contention, sourced
from telemetry, never invented).

Prereq: the `agentosd keyhole` + `agentosd telemetry` producers must be running
(the MCP reads `keyhole.json` + the `coexist` plan over `telemetry.jsonl`; it
touches no NVML/D-Bus/network itself). Register the server with Hermes:

```bash
# add the stdio server (discovery-first: probes it, lists the 3 tools).
# answer Y at the "Enable all 3 tools?" prompt.
hermes mcp add agentos --command /home/corey/.local/bin/agentosd --args mcp
# (or, against the venv directly:)
#   ~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main mcp add agentos \
#     --command /home/corey/.local/bin/agentosd --args mcp
```

Persists to `~/.hermes/config.yaml` under `agentos:` (`command`/`args`/`enabled`).
Verify + restart to load into the gateway (Telegram):

```bash
hermes mcp test agentos        # expect: ✓ Connected, 3 tools discovered
systemctl --user restart hermes-gateway.service
grep "MCP server 'agentos'" ~/.hermes/logs/agent.log | tail -1
# expect: registered 3 tool(s): mcp_agentos_gpu_status, _residency, _why
```

Then from any session (e.g. Telegram): *"how much GPU VRAM is free?"* invokes
`gpu_status`. **Perceive-only** — the act verbs (`gpu_request`/`gpu_release`) are
gated behind the ADR-0021 act-tier/identity plumbing, not exposed here.
Remove with `hermes mcp remove agentos`.

## Activate

```bash
systemctl --user restart hermes-gateway.service
```

## Verify

- **API bound:**

  ```bash
  ss -tln | grep :8642
  ```

- **Auth works:**

  ```bash
  curl -s -H "Authorization: Bearer $(grep '^API_SERVER_KEY=' ~/.hermes/.env | cut -d= -f2)" \
    http://127.0.0.1:8642/v1/capabilities
  ```

- **Plugin loaded:**

  ```bash
  grep gpu-coordinator ~/.hermes/logs/agent.log | tail -1
  # expect: "llm_execution middleware registered"
  ```

- **Lease persistence (the bug we fixed):** fire one inference, then watch
  `Status` hold **ONE** token across the 60s `Renew` boundary with **no**
  `renew_failed` in `agent.log`:

  ```bash
  busctl --user call org.agentos.Coordinator1 /org/agentos/Coordinator1 \
    org.agentos.Coordinator1 Status
  ```

  NOTE: api_server turns are slow (~110–210s on qwen3.6-27b, owing to a ~15k-token
  system prompt) — drive verification with `curl -m 240`.

## Per-call tier (ADR-0041)

The wrap now picks the lease tier **per call** instead of always `interactive`:

- a **live** turn (cli/telegram/discord/… — a human waiting) → `interactive` (preempts the dream);
- a **background** turn whose `platform` ∈ `AGENTOS_GPU_BATCH_PLATFORMS` (default `cron,subagent`) →
  `batch` (queues behind live turns, shares the heavy lane with dreaming via the ADR-0041 arbiter).

The default set is deliberately conservative — only platforms that are *never* a live human turn — so a
live turn can never be mis-tagged `batch` (which would make the user yield to the dream). Tune it:

```bash
# extend/replace the background set (comma list); empty string ⇒ interactive-always (pre-ADR-0041)
AGENTOS_GPU_BATCH_PLATFORMS=cron,subagent
```

Verify on-box which platform YOUR overnight/agentic inference actually carries (`grep "platform=" …`)
and extend the set if needed — `platform` inherits the kanban submitter, so a task created from Telegram
reads `telegram`, not `cron`. (NB: a per-call `task_id` is NOT a usable signal — Hermes fills a UUID for
live turns, so it can't separate batch from live.)

## Kill-switch / rollback

- **Disable without uninstalling** — set the env kill-switch so the hook becomes a
  pure passthrough:

  ```bash
  AGENTOS_GPU_COORD_DISABLE=1
  ```

- **Full removal:**

  ```bash
  # 1. remove gpu-coordinator from plugins.enabled in ~/.hermes/config.yaml
  rm -rf ~/.hermes/plugins/gpu-coordinator
  # 2. remove the API_SERVER_* lines from ~/.hermes/.env
  systemctl --user restart hermes-gateway.service
  ```

  Backups taken 2026-06-19: `~/.hermes/{config.yaml,.env}.bak.agentos_*`.
