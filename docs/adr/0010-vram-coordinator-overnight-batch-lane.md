# ADR-0010: VRAM coordinator — preemptible leases + an overnight batch lane

- Status: Accepted (core slice implemented + GPU-validated 2026-06-16; see Implementation status)
- Date: 2026-06-16
- Relates to: ADR-0001 (substrate, not orchestrator), ADR-0003 (fail-open supervised),
  ADR-0004 (graphics yield: kill/relaunch), ADR-0006 (Hermes plugin → D-Bus lease/priority),
  ADR-0009 (dreaming is a coordinator-governed consumer)

## Context

The VRAM coordinator is the substrate's headline deliverable and is still **zero lines of
code** — `crates/agentosd/src/main.rs:16` ships a read-only monitor ("No eviction, no
`ollama stop`, no nimbus-flux kill/relaunch yet"). Two facts now fix its shape:

1. **The graceful release lever doesn't work.** `POST /free` was measured freeing **0 VRAM**
   on this box (ADR-0009). The only trustworthy reclaim is owning a process's PID and killing
   it. So eviction must be **own-PID + SIGKILL**, not a polite ask.
2. **The operating model is an overnight batch, not daytime contention.** Dreaming generation
   runs up to ~1 hour per clip (Wan 14B, the quality pick), and several GPU-heavy agents run
   **overnight in sequence to respect VRAM**. During the day, interactive inference owns the
   GPU; the wallpaper plays from a cache filled overnight. This collapses the hard case
   (dreaming vs live inference fighting over 24 GB) into a scheduling problem with a rare
   wake-up backstop.

ADR-0006 already routes Hermes → a D-Bus lease/priority call → agentosd. This ADR specifies
the agentosd side: the lease, its priority order, admission control, the evict lever, and the
overnight lane.

## Decision

1. **One exclusive, preemptible VRAM lease, held by agentosd.** Heavy GPU consumers (a video
   generation, an overnight model job, the desktop's RT effects) acquire the lease before they
   allocate. agentosd grants it to **one holder at a time** — this is what makes "in sequence
   to respect VRAM" *enforced*, not hoped.

2. **Priority order (a higher tier preempts a lower one):**
   `interactive/live inference  >  overnight batch (dreaming + other GPU agents)  >  best-effort`.
   Live inference always wins; an interactive request mid-overnight-window **preempts** the
   batch holder.

3. **Hermes schedules; agentosd serializes** (don't reinvent — ADR-0001/0006). Hermes' cron
   fires the overnight agent sequence; each GPU-heavy job acquires the agentosd lease (via the
   Hermes plugin, ADR-0006) before allocating. **agentosd is not a job scheduler** — it is the
   gate that admits one heavy consumer at a time and evicts on preemption.

4. **Admission control — predict before load.** Grant a lease only if the estimated footprint
   fits measured free VRAM (with headroom, ADR-0004's real-data margins); otherwise queue or
   refuse. Never "start and regret" — the substrate exists to prevent the OOM, not survive it.

5. **Eviction = own-PID + SIGKILL.** agentosd owns the lifecycle of the processes it can evict:
   it spawns ComfyUI and holds its PID (ADR-0009); it already plans `ollama stop` (model
   unload) and nimbus-flux kill/relaunch (ADR-0004). `POST /free` is a best-effort *hint*
   tried first, never trusted (measured: freed 0 VRAM).

6. **The overnight batch lane.** A nightly window gives the GPU to the batch queue. Dreaming is
   one lease-holder, generating clips into the dream cache (ADR-0009 Surface A). The same lane
   also carries the **autonomous development pipeline's council passes (ADR-0011)** —
   local-model inference jobs — multiplexed through the one lease alongside dream generation, one
   heavy consumer at a time. A holder in progress when an interactive request arrives is
   **SIGKILLed; prior cached output persists; the job requeues** for the next window. Fail-open
   (ADR-0003): if the coordinator is down or unsure, the batch simply does not run — interactive
   is never blocked, and the wallpaper falls to the shader.

7. **Async-runtime shift (enabling implementation).** Introduce `tokio`; the daemon becomes a
   `select!` over the NVML interval tick, the D-Bus lease server (ADR-0006), the process
   supervisor (owned children), and a shutdown signal. NVML (`nvml-wrapper`), `rusqlite`, and
   blocking HTTP stay behind `spawn_blocking`; lease decisions are deterministic budget math,
   not heuristics (model proposes / code disposes).

## Open questions (human disposes)

- **Window trigger:** a fixed nightly cron hour (simple, deterministic — recommended to start),
  vs idle-detection gating (no interactive inference for N minutes), vs Hermes decides. Lean:
  fixed window now, add idle-gating later.
- **What dreaming produces per night:** one fresh dream texture, vs a small per-state pool
  (idle/working/needs_you/snag-appropriate). Lean: start with one fresh texture (dream-as-
  texture means the shader supplies the per-state variation), grow to a pool.
- **Day-one scope:** do non-dreaming overnight agents acquire the same lease via the Hermes
  plugin (ADR-0006) immediately, or does the lease ship first serving only dreaming + RT
  effects? Lean: ship the lease for dreaming + RT first, wire the Hermes-plugin acquirers next.

## Consequences

- **This is the substrate floor.** It converts ADR-0009's `[SUBSTRATE-BLOCKED]` items
  (admission control, SIGKILL, mutual exclusion) into buildable work, and gives ADR-0006's
  Hermes plugin a concrete lease to call.
- **Determinism/safety:** the lease grant/deny/evict decision is deterministic VRAM math; no
  model output reaches it. SIGKILL of an owned PID is the one irreversible act and it is
  gated by that math, never by a model.
- **Reversibility:** preempting a dream loses an in-flight clip only (a cache artifact, ADR-0009)
  — no system/desktop state is mutated, so preemption is safe and needs no rollback.
- **Scope discipline:** agentosd gains a lease server + process supervision, not a scheduler or
  a queue engine. The overnight *sequence* is Hermes' cron; agentosd only enforces *one at a
  time* and *who yields to whom*.
- **Next:** an implementation slice — own the ComfyUI PID, NVML-gated admission for a single
  lease, SIGKILL on preempt — provable against the real GPU the way the monitor already is.

## Implementation status (2026-06-16) — core slice landed + GPU-validated

The "Next" slice is built: **`agentosd coord`** (`crates/agentosd/src/coord.rs`). It is the
floor under ADR-0009/0010/0011 — the load-bearing mechanism is now real code, not a premise.

**Built and proven against the live RTX 4090** (the way `monitor` proves the read path):

- **Predict-before-load admission (§4)** — `admit(free, est, headroom)` is pure, saturating,
  deterministic budget math (no model output reaches it). Validated both ways on the box: a
  2000 MiB estimate against 18.7 GB free → **GRANT**; a 999999 MiB estimate → **DENY (short
  ~982 GB), refuses to spawn, exits 0**. Declining the OOM is the success path.
- **Own-PID + SIGKILL evict (§5)** — `coord` spawns and owns the child (`kill_on_drop` as the
  fail-safe backstop, ADR-0003). `POST /free` is never on this path. SIGUSR1 (a stand-in for
  the interactive request that ADR-0006's D-Bus lease will carry) drives the full preempt path:
  `arbitrate(Batch, Interactive) → Preempt → SIGKILL → before/after VRAM read → release`.
- **Priority arbitration (§1/§2)** — `Tier` (`Interactive > Batch > BestEffort`) + `arbitrate`,
  exhaustively unit-tested.
- **Async-runtime shift (§7)** — `tokio` introduced; the supervisor is a `select!` over the NVML
  interval tick, owned-child exit, the preempt signal, and shutdown (SIGINT/SIGTERM). NVML runs
  behind `spawn_blocking`. `monitor`/`feed` keep their blocking loops untouched.
- **Unified coordinator daemon (ADR-0006 + §1–§6)** — `agentosd lease`
  (`crates/agentosd/src/lease.rs`) is `coord` × `lease` merged into one process: it serves
  `org.agentos.Coordinator1` on the session bus (zbus, tokio reactor) **and** owns the batch
  children, so a preemption actually SIGKILLs the running job. Two holder kinds:
  `Acquire(tier, est)` = cooperative (caller owns its own process — Hermes inference; agentosd
  owns nothing, top-tier so never killed) and `Spawn(tier, est, argv)` = owned (agentosd
  spawns + holds the PID — ComfyUI/batch). Plus `Release(token)` and `Status()`. Backed by the
  *same* `admit` + `arbitrate` core plus a pure, tested `LeaseState` (single exclusive lease,
  monotonic tokens). A background supervisor reaps a naturally-exited owned child and
  auto-releases its lease.
- **Proven live via `busctl`:** the lease protocol (grant → queue → preempt → stale-release
  rejected → release → deny), AND the headline merge — `Spawn` a batch child, then a cooperative
  `Acquire(interactive)` **SIGKILLs that owned PID** (verified gone via `pgrep`) and grants the
  interactive holder; a `Spawn`ed job that exits on its own is **auto-released** by the
  supervisor. Whole crate: **33 tests green, clippy clean.**

**Deliberately NOT yet (`[SUBSTRATE-BLOCKED]` resolved here; remaining wiring):**

- **Spawning ComfyUI specifically** — `Spawn` owns *any* `argv`; wiring the real ComfyUI
  invocation + the dream cache write (ADR-0009) is next.
- The **Hermes plugin** (ADR-0006) — the daemon now exists for it to call; the plugin
  (`llm_request` priority tag, `llm_execution` `Acquire`/`Release` around the call) is unbuilt.
  This is the last hop to real end-to-end serialization of Hermes inference vs. overnight batch.
  **Update (2026-06-19):** the plugin is now **built and committed** (`integrations/hermes/
  gpu-coordinator/`); `llm_execution` wraps every call in `Acquire`/`Release` (the priority
  tag stays unregistered pending the DEFERRED ADR-0002 proxy). The true remaining hop is
  **install** — `gpu-coordinator` is not yet enabled in the live `~/.hermes/plugins/` (only
  `needs-you-signal` is), so end-to-end serialization is test-proven but not production-active.
- The **overnight batch lane / window trigger** (§6, Open questions) — Hermes' cron + kanban
  drive the sequence; agentosd only enforces one-holder-at-a-time. Not yet scheduled.
- **No revoke signal / wait-queue.** A losing acquirer is told `queued` and must retry; there is
  no D-Bus signal for cooperative holders and no FIFO wait (real backpressure comes from the
  gateway holding inference responses, ADR-0006). `LeaseDecision::Queue` is tested.
- **Lease-leak on a crashed cooperative holder** — a cooperative `Acquire`er that dies without
  `Release` leaks the lease (D-Bus peer-disconnect auto-release is a future hardening). Owned
  (`Spawn`) jobs don't have this — the supervisor reaps them.
- **`coord` (standalone) is now superseded** by the daemon for production; it remains as a
  no-D-Bus demo of single-PID supervision (SIGUSR1-triggered) and shares the same pure core.

## Review & hardening (2026-06-16)

A 4-reviewer panel (resource-safety · rust · security · determinism) audited the slice —
[scorecard](../research/0005-coordinator-review-scorecard.md). The pure core was rated excellent;
the impure shell had two Critical + five High. **Fixed + re-tested this pass** (36 tests, clippy clean):

- **R1 — group-kill.** Owned children spawn in their own process group (`process_group(0)`); eviction
  SIGKILLs the whole group (`kill(-pgid)`), so a launcher that forks workers (ComfyUI) is fully
  reclaimed — not just the direct PID. *Proven live*: a forked-grandchildren job is wiped on preempt.
- **R2 — fail-open restored.** An unreadable NVML no longer coerces to `free=0` (which would *deny
  interactive*). Interactive fails **open** (grant); batch/best-effort fail **closed** (don't start
  blind). Honors ADR-0003.
- **H1 — preempt decision completed.** After arbitration decides yield, the successor's fit is
  re-checked against *predicted* post-eviction free VRAM (current + the victim's admitted estimate,
  carried in the lease) via the pure, tested `fits_after_evict`; a `WONT-FIT` grant is surfaced +
  logged loud (interactive still wins — top tier — but the caller is told to offload/shrink).
- **H2 — honest reclaim.** The evicted child is `wait()`-reaped (no zombie) *before* the reclaim Δ
  is read, replacing the fixed-300 ms guess.
- **H3 — atomic-ish preempt.** A bad `argv` is pre-flighted (`looks_executable`) *before* any
  eviction, so a typo can't destroy the incumbent.
- **H5 (partial) — scaled headroom** (`max(512, est/16)`); the full per-`NUM_PARALLEL` KV model is
  deferred to an ADR amendment.

**This correction to the contract:** the *grant* is gated by VRAM math; the *preempt + kill of the
lower tier* is gated by **priority**, now with a computed fit verdict surfaced to the successor
(earlier text over-promised "the SIGKILL is gated by that math").

**Deferred to [ADR-0013](0013-coordinator-ipc-trust-and-lease-lifecycle.md) (needs a decision):**
the IPC trust model (S1 — unauthenticated `Spawn`-arbitrary-argv on the session bus) and lease
lifecycle (H4 — crashed-holder reclaim / TTL; M4 — anti-strobe dwell). These change the ADR-0006
plugin call contract, so they are ADR-before-code.
