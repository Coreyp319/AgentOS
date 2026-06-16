# Hermes integrations (no-fork glue — ADR-0006)

AgentOS attaches to the already-installed Hermes runtime via **plugins**, never a fork.

## `needs-you-signal` (P2)

A standalone observer plugin that mirrors Hermes' in-memory pending-approval state
to a file the AgentOS producer reads.

- **Hooks:** `pre_approval_request` (add) + `post_approval_response` (clear on
  approve/deny/timeout). Observer-only — it never vetoes or delays an approval.
- **Why a plugin:** Hermes keeps pending command-approvals only in the gateway
  process's RAM (`tools/approval.py`); no DB row, no field in `gateway_state.json`,
  no HTTP endpoint. The only way to observe "Hermes is waiting on the user" out of
  process is to subscribe to the approval hooks.
- **Contract — `~/.hermes/needs_you.json`:**
  ```json
  {"pending": 1, "updated_at": 1718400000.0,
   "items": [{"session_key": "...", "surface": "gateway",
              "description": "...", "command": "...", "since": 1718399990.0}]}
  ```
  `agentosd feed` reads `pending`; when > 0 **and the gateway is alive** it emits the
  `needs_you` (warm) ambient state. Approvals are only raised when
  `approvals.mode` ≠ `off` (and not YOLO/allow-listed) — i.e. exactly when Hermes is
  genuinely blocked on you.

### Install / uninstall

```sh
integrations/hermes/install.sh     # copy → ~/.hermes/plugins/, enable, restart gateway
integrations/hermes/uninstall.sh   # disable, remove, restart gateway
```

Enabling a plugin requires a **gateway restart** (Hermes discovers plugins once at
startup), which briefly interrupts running agents — run it when the fleet is idle.

### Staleness

State is in-process, so a gateway crash mid-approval could leave a stale
`needs_you.json`. Mitigations: `register()` rewrites an empty file on clean startup,
and the producer ignores `needs_you` unless `gateway_state.json` reports the gateway
alive.
