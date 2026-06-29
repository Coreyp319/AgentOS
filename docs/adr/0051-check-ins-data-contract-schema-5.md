# ADR-0051: The Check-ins data contract — keyhole.json schema 5 (per-task rows, honest GPU-util, recurring)

- Status: **Proposed** — built this session (Phase A, the read-only floor). The contract the
  Check-ins tab (ADR-0050) renders.
- Date: 2026-06-29
- Deciders: Corey (binding product steer: "we'll need many ADRs"; "at the very least" a buildable
  read-only floor), design synthesis from three Plan agents.
- **Extends ADR-0012** (the read-only `keyhole.json` producer, `crates/agentosd/src/keyhole.rs`),
  following the **schema-2/3/4 additive precedent** (`pending_requests`, `workload`, `queue` — each
  appended, older consumers ignore trailing fields). **Supersedes nothing.** Relates ADR-0019 §6 /
  ADR-0041 (the sibling read-only mirror blocks `pending_requests` / `queue` this sits beside),
  ADR-0003 (fail-open), ADR-0001 (we mirror Hermes' rows, we do not re-derive the kanban).

## Context

`keyhole.json` is at **schema 4** (`const SCHEMA: u32 = 4`). The per-task rows the Check-ins cards
need **do not exist** in the contract: `feed.rs` reads `kanban.db` only as `COUNT`/`SUM` aggregates
(`FLEET_SQL` → `{total,running,snagged,pending}`), never per-task. A real GPU utilization % **is**
computed every tick (`read_gpu_util` → `gpu_work_level`) but only feeds the work-gate — it is never
serialized; the cards' metrics rail has no honest source today. `tokens_per_sec` is hard-`null`
(needs the ADR-0002 proxy). So the cards would have nothing real to render. This ADR adds exactly
the read-only datums they need, and nothing it cannot source.

## Decision

1. **Bump `SCHEMA` 4→5; append after `queue` (additive — older consumers ignore trailing fields,
   the same guarantee schema 2/3/4 relied on).** Four additions, all read-only:

2. **`gpu_util_pct` (top-level `i64`).** The already-computed util, finally serialized: `Some(u) → u`,
   `None → -1`. **Honest by construction:** a real `0` means an *idle* GPU; `-1` means NVML was
   *unreadable*. The same `-1`-is-UNKNOWN-not-zero rule the fleet/vram fields already hold (§4). This
   is the metrics rail's real GPU number — no longer derived-only.

3. **`check_ins` — an array of per-task `CheckIn` rows**, read **read-only** from `kanban.db tasks`
   via a new `read_check_ins()` that **reuses `feed.rs`'s exact pattern**:
   `Connection::open_with_flags(…, SQLITE_OPEN_READ_ONLY)` + `busy_timeout(2000)`. Each row ships
   **raw** kanban fields — at minimum `status`, `consecutive_failures`, a title/id, and the relevant
   epoch timestamps — **never a UI mood word.** The view (ADR-0052) derives mood
   (`calm/working/stalled/needsyou/done`) from `status` + `consecutive_failures`; the producer never
   emits UI vocabulary, so the contract stays a data feed, not a presentation. Rows are ordered
   **active-first** (running → blocked → review → … → done) and **capped at 16**; a recently-`done`
   task is included only within a **6h window** (for the Check-ins DONE column), so the array reflects
   live work, not the whole history.

4. **`check_ins_total` (`i64`).** The pre-cap count via `COUNT(*) OVER ()`, so the card view can say
   an honest *"16 of 23"* rather than implying the capped 16 is everything; `-1` = unknown (DB
   unreadable), consistent with the sentinel rule.

5. **`recurring` — an array** sourced from `~/.hermes/cron/jobs.json` (`schedule_display`,
   `next_run_at`, `last_status`) for the recurring/cron checks the Check-ins view shows alongside
   one-shot tasks. Read-only, fail-soft like every other source. (The cron source already exists,
   so cadence detail may fold into this contract rather than a separate ADR — see Status / next,
   ADR-0056.)

6. **Honesty rules carried verbatim from the existing producer.** Timestamps are epoch **seconds**
   (Hermes writes `int(time.time())`); `-1` = a stamp that **never happened** (distinct from a real
   `0` epoch). `tokens_per_sec` **stays `null`** — never synthesized; it graduates only with the
   ADR-0002 proxy (ADR-0055). There is **no `$budget` field**: AgentOS has no cost source today, so
   we **omit it, never fabricate it** (the cost tile waits for ADR-0055). The honest-empty rule: the
   **only** empty `check_ins` is a genuinely empty fleet — never a fabricated placeholder task.

7. **Fail-open safety (ADR-0003), no panic.** `SQLITE_OPEN_READ_ONLY` cannot lock, checkpoint, or
   wedge Hermes' WAL. DB absent / locked / corrupt → `read_check_ins()` returns
   `.unwrap_or((vec![], -1))` (empty rows, `-1` total) — fail-open, never a crash of the producer.
   Extend the existing one-shot **startup schema-drift probe** (`probe_fleet_schema` / `FLEET_COLUMNS`)
   to cover the **new per-card columns**, so a Hermes upgrade that renames/drops a column logs
   **loudly** in the journal instead of silently emptying the Check-ins tab (the same "phantom calm"
   trap `feed.rs` already guards the aggregate read against).

8. **The two empty states stay distinct with no new flag.** An **unreachable** Hermes already sets
   `gateway`/`state` = `"unknown"` (§4) → the view renders *"Can't reach Hermes."* A **reachable but
   empty** fleet leaves a real `state` (e.g. `idle`) with `check_ins: []` → the view renders *"No
   active check-ins."* The existing honesty fields disambiguate; no extra boolean is added.

## Consequences

- **One extra indexed `SELECT` per existing 2/5/10s tick.** No new poll loop — it rides the
  keyhole's existing adaptive cadence; `idx_tasks_status` covers the active-first ordering, so the
  cost is negligible and the slow-NVML backoff still throttles it under load.
- **The cards ride the existing edge-dedup** — `KeyholeFeed` derives `PartialEq`, so a tick whose
  `check_ins` are byte-identical rewrites nothing; the tab only repaints on a real change.
- **The repurposed metrics rail (GPU-util % / VRAM / active-count) now has a real, non-faked source
  today** — `gpu_util_pct` + the existing `vram` + `check_ins` cardinality. tok/s and $budget remain
  honestly absent until ADR-0055.
- The `tasks`-table read is direct-into-Hermes' internal schema (no API boundary), so the drift probe
  is the load-bearing guard; it is diagnostic-only and the runtime path stays fail-open.

## Status / next

Proposed; Phase A read-only. The contract must be pinned the same way schema 1–4 are — extend
`pins_the_exact_contract` (the serde round-trip exact-string test) to schema 5 and update
`KeyholeModel.qml` in lockstep. Deferred companions: **ADR-0055** (graduate `tokens_per_sec` off
`null` when the ADR-0002 proxy lands + add the cost/$budget tile, Phase C); **ADR-0056** (recurring /
cron cadence — but the source is already `~/.hermes/cron/jobs.json`, so the `recurring` block here may
make a separate ADR unnecessary). Writes remain out of scope (ADR-0053); the write client is
**ADR-0054** (Phase B).
