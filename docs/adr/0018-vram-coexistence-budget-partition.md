# ADR-0018: VRAM coexistence — a warm-pool / heavy-lane budget partition, measured before tuned

- Status: Proposed (telemetry substrate — the measurement slice — implemented 2026-06-16;
  the partition policy + residency tuning + live-test harness are sequenced behind the data)
- Date: 2026-06-16
- Relates to: ADR-0001 (substrate, not orchestrator), ADR-0002 (configure Ollama, don't
  reinvent residency/concurrency), ADR-0003 (fail-open supervised), ADR-0004 (graphics yield),
  ADR-0010 (the one exclusive preemptible lease this extends — it stays correct for the heavy
  lane), ADR-0012 (the keyhole snapshot; this telemetry log is its time-series sibling).

## Context

ADR-0010 gave the coordinator one exclusive, preemptible VRAM lease with own-PID + SIGKILL
eviction. That is the right model for **heavy, mutually-exclusive** consumers (a Wan dream job,
ComfyUI, the RT wallpaper). But the day-to-day ask is different: keep the **frequently-used
models warm so they coexist**, and only fall back to eviction at the margins. Three measurements
on the target box (RTX 4090, 24 GB; taken 2026-06-16) fix the shape of that policy:

1. **Graphics is the dominant baseline, not the models.** With *zero* LLMs resident, Plasma + the
   Nimbus reactive-wallpaper shader already hold **~7.7 GB**. The effective LLM/compute budget is
   ~16.3 GB (RT on) / ~17.8 GB (wallpaper yielded, −1.5 GB per ADR-0004). The single biggest lever
   on "what can coexist" is the graphics footprint, not the model mix.

2. **The local models are big relative to the card, so coexistence is bounded.** Hermes-4.3-36B Q4
   = 21.8 GB (does **not** fit fully even with the wallpaper yielded — it spills to CPU); 27B = 17.4 GB
   and 14B-Q8 = 15.7 GB are effectively **exclusive** (one resident at a time, graphics-yielded);
   only the ≤10 GB tier (e.g. gemma4-8B, ~5 GB resident) **genuinely coexists** — two small models
   plus graphics fit at once. "A warm pool of three big models" is not physically available here.
   The win is keeping the *right* (small, frequent) models warm and swapping the big ones through
   the lease.

3. **`ollama stop` is a trusted graceful release lever — `POST /free` was not.** ADR-0010 §5
   measured `POST /free` freeing **0 MiB** (hence own-PID SIGKILL). Re-measured on this box,
   `ollama stop <model>` fully returned VRAM (16301 ≈ 16232 MiB baseline; `/api/ps` empty). It is a
   different code path and it works. This gives the warm pool a *graceful* evictor that does not
   require owning Ollama's PID.

Two gaps block acting on any of this. (a) The coordinator models VRAM as a **single scalar** to
admit against; it has no notion of a coexisting warm set vs. an exclusive lane. (b) The substrate
keeps **no history** — `monitor` prints to stdout, `keyhole.json`/`agent.json`/`lease.json` are
current-snapshot files. We cannot tune residency or validate a policy on data we never recorded.
Note too that Ollama's reported `size_vram` **undercounts** the real footprint (3.39 GB reported
vs. ~5 GB GPU delta on the gemma4 load), so admission estimates built from it alone are optimistic.

## Decision

1. **Model VRAM as two regimes sharing one budget, not one scalar.**
   - **Warm pool** — the frequently-used small models, **multi-resident**, managed by Ollama's own
     residency/concurrency (`OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_KEEP_ALIVE`, `OLLAMA_NUM_PARALLEL`).
     Per ADR-0002 this is **config, not new code** — agentosd tunes it, it does not reimplement it.
   - **Heavy lane** — big-model swaps, dream/ComfyUI jobs, RT graphics: **exclusive**, arbitrated by
     the existing ADR-0010 lease. ADR-0010 is not superseded; it governs this lane unchanged.

2. **Graceful reclaim before the sledgehammer.** When the heavy lane needs the card, the coordinator
   first reclaims the warm pool with `ollama stop` (cold models first), re-checks fit, and only then
   reaches for SIGKILL. SIGKILL remains the backstop for the off-lease / unreliable case (ADR-0003).

3. **Measure before tuning — the telemetry substrate is the first deliverable (implemented now).**
   A new read-only `telemetry` producer mode appends one JSON line per tick to a **persistent**
   `$XDG_STATE_HOME/agentosd/telemetry.jsonl` (survives reboot, unlike the runtime snapshots),
   size-rotated. Per tick: VRAM used/free/total, per-process attribution (graphics vs compute, by
   name — so the ~7.7 GB baseline is itemised), GPU util/power/temp, Ollama residency (name +
   reported `size_vram` + loaded_secs), load/unload events (diffed across ticks → load latency &
   churn are greppable), the lease mirror, and `tokens_per_sec` (null until the ADR-0002 proxy
   feeds it — **never synthesised**, same honesty rule as the keyhole). This is the evidence base
   for §1, §4, and the live-test harness.

4. **Residency tuning is model-proposes / code-disposes and reversible.** The warm-set composition
   (which models stay warm, the keep-alive, max-loaded) is *derived from the measured frequency and
   footprint distribution*, applied by writing Ollama config / env, and reversible. No model output
   reaches a VRAM action without a deterministic fit gate (ADR-0010 admission core).

5. **A live-test harness validates the policy against reality** (sequenced last): repeatable
   contention mixes (N concurrent LLM calls + a dream job + wallpaper on/off) measured from the
   telemetry log — OOM near-misses, load p50/p95, eviction churn, time-to-first-token, tokens/sec
   under contention.

## Consequences

- The telemetry log is **append-only and read-only of the system** — it observes, it never acts; a
  failed write degrades to a warning and the loop continues (fail-open, ADR-0003). It holds no
  secrets (VRAM counters, model names, process names) and lives under the user's state dir.
- "Coexist optimally" is honestly bounded on this hardware: **one big resident model + warm-swap +
  graphics-yield**, plus genuine coexistence only at the ≤10 GB tier. The product language must not
  promise a warm pool the 24 GB card cannot hold.
- Disk: at the 2 s default cadence the log is ~10–20 MB/day; rotation at 64 MiB keeping one prior
  generation bounds it to ~7–9 days of history — enough for a tuning/validation window, not a
  forever archive.

## Non-goals

- Not reimplementing Ollama residency/concurrency/queueing (ADR-0002). agentosd tunes config and
  owns only the cross-runtime arbitration Ollama cannot see.
- Not a metrics server (no Prometheus/Influx/daemon). A JSONL the user can `jq`/`duckdb` is the
  right altitude for a single-box substrate; a real TSDB is out of scope until proven necessary.
- Not changing the heavy-lane lease semantics of ADR-0010.

## Review panel resolutions (persona + design panel, 2026-06-16)

A reviewer panel (resource-safety, determinism-safety, rust-performance, responsible-AI/privacy,
ai-product, personalization-loop) + an ambient-legibility lens reviewed Phases 1–2 and the Phase 3
plan. Phases 1–2 architecture cleared; the following are now binding.

**Sequencing — experiment before coordinator surgery.** The deployed Ollama runs on *defaults*
(`MAX_LOADED_MODELS=3`), not the repo's recommended `=1` (the recommendation was never applied). So
coexistence is silently on-and-unmanaged. Before building Phase 3, run the cheap experiment: set a
*deliberate* config (`MAX_LOADED=2`, `KEEP_ALIVE=30m`, `NUM_PARALLEL=2`), let the existing telemetry
record real use, and gate Phase 3 on two **measurable-today** signals — avoided-swaps and
admission-refusals / OOM-near-misses. (TTFT / tokens-sec need the unbuilt ADR-0002 proxy — not a gate.)

**Privacy posture (Phase 1, now implemented).** The log is a behavioral trace, so: `0700` dir /
`0600` file (don't trust umask; fixes pre-existing world-readable artifacts on start); the itemised
`procs[]` names ONLY the processes agentosd arbitrates (`ollama`/`nimbus`/`comfy`/`python`) and
buckets everything else to `other-gfx`/`other-compute` — the per-kind totals (and thus the budget
math) are preserved, third-party app identities are not (the analyzer never read names anyway); an
install + startup disclosure of what is recorded and how to stop/forget it. Retention is the
size-rotation window (~7–9 days); a standalone purge is documented. (cf. ADR-0016 ephemeral-mode bar.)

**Phase 3 acceptance criteria (must hold before the evictor influences a real admission).**
1. *Don't reinvent admission.* Keep admission **scalar + live-NVML**; do NOT build a "coexistence-aware"
   admission that tracks the warm set's composition (a second VRAM accountant that drifts from NVML and
   from Ollama). The only coexist input admission needs is the corrected per-model **footprint** as a
   better `est_mib` for a big-model swap.
2. *Measure, don't predict, after `ollama stop`.* agentosd doesn't own Ollama's PID (no `wait()`), so
   poll free VRAM until it rises / `/api/ps` empties (timeout-bounded), then run the real `admit` — the
   learned footprint is the poll target, never a substitute for the post-stop measurement.
3. *SIGKILL asymmetry is explicit.* Warm-pool eviction is `ollama stop` ONLY (killing the daemon kills
   the runtime). If post-stop still won't fit → deny (fail-closed for batch). SIGKILL stays the backstop
   only for *owned* heavy holders, unchanged from ADR-0010.
4. *Clamp the learned footprint.* It may only ever RAISE the reservation: `max(measured, reported ×
   undercount)`, never below raw `size_vram`; use **max/p90, not median**, of load-deltas for the safety
   gate (median is for the report); cap undercount to ~[1.0, 2.0]; reject `> total − safety`; gate on a
   per-model sample count (`footprint_samples`); relearn when `NUM_PARALLEL` changes.
5. *Live re-check under the lock.* The learned plan is a proposal; the deterministic `admit` /
   `fits_after_evict` against measured free VRAM under the `Inner` mutex is the gate (no TOCTOU).
6. *Anti-strobe dwell* on warm-pool eviction (don't re-warm a just-stopped model / re-stop immediately).
7. *Apply is its own tiny reversible step* (snapshot env → write → restart); pin/exclude overrides live
   in Hermes memory (ADR-0006), not a new agentosd config store.

**Legibility grammar (folds into §2).** Two kinds of GPU motion, two grammars: the heavy-lane preempt
is an **event** (`lease.preempt`, one *outcome* sentence — "made room for X, freed ~N GB"); routine
warm-pool churn is **state** in the existing `residency[]` view, never narration (avoid the anxiety
ramp). Exactly one earned interruption: when the *active interactive* model is evicted (a felt reload
latency), a single calm swaync toast (cool, not `needs_you`-warm). Routine churn stays silent. The
residency view must distinguish honest-empty from unknown. This is a minor keyhole field addition, not
a full design-council pass.

**Phase 1–2 hardening applied this pass:** `0700`/`0600` perms + name bucketing + disclosure
(privacy); `total_cmp` NaN guard; rank by recency-weighted `loads` not `ticks_resident` (kills the
keep-alive feedback loop); recency half-life decay; confidence-gated config block (keep Ollama defaults
below `MIN_SAMPLES`); baseline fallback when no idle ticks; `footprint_samples` carried for the Phase-3
gate.

## Implementation status (2026-06-16)

- **Done (Phase 1, §3):** `agentosd telemetry` read-only historian — `crates/agentosd/src/telemetry.rs`,
  appends `telemetry.jsonl` with per-process VRAM attribution, GPU util/power/temp, Ollama
  residency, load/unload events, and the lease mirror; size-rotated; `--once` / `--interval`.
  Installed as the `agentos-telemetry.service` `--user` unit (default.target, Nice=15, idle IO) via
  `dist/{apply,restore}.sh`.
- **Done (Phase 2, §4):** `agentosd coexist` read-only analyzer — `crates/agentosd/src/analyze.rs`,
  reads the log and *proposes* (never applies) a residency plan: warm-pool vs heavy-lane
  classification by footprint, `MAX_LOADED_MODELS`/`KEEP_ALIVE`, and each model's **real** admission
  footprint with the `size_vram` undercount *learned from the load deltas* (measured ×1.42 on first
  real data, vs the ×1.45 estimate). Pure aggregation/selection core, unit-tested.
- **Hardened (this pass, per review):** privacy (0700/0600 + name bucketing + disclosure), `total_cmp`
  NaN guard, rank-by-recency-weighted-loads, confidence-gated config, baseline fallback.
- **Running now:** the config experiment (`MAX_LOADED=2`/`KEEP_ALIVE=30m`/`NUM_PARALLEL=2`) with the
  telemetry recording — gates the Phase 3 go/no-go on avoided-swaps + OOM-near-misses.
- **Next (gated on the experiment):** the narrow `ollama stop → measure → re-`admit`→ deny/SIGKILL`
  evictor (§2 + acceptance criteria); feed the corrected footprint into the *existing scalar* admission
  (NOT a warm-set accountant, §1); the gated/reversible config *apply*; the live-test harness (§5).
