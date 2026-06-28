# ADR-0041: A real cross-workflow VRAM-demand queue — an AgentOS-owned arbiter, not the SIGKILL daemon and not Hermes

- Status: **Proposed — council-ratified; Phase 0 + Phase 1 BUILT (uncommitted), GO/NO-GO re-review
  PASSED** (model proposes, code disposes; the human disposes on this). Phase 0a (over-admit fix), 0b
  (`freed_seq`), 0c (per-connection authz, no socket) + Phase 1 (the `agentosd queue` arbiter, pure core
  + shell) are built and 3-lens-reviewed GO-AFTER-FIX (all fixes applied; 217 tests, clippy clean).
  Remaining: Phase 2 (wire demand sources + harden the ADR-0036 lane library) and the optional Hermes
  plugin tier fix; on-box deploy + live e2e is the user's call. The user steered the decision explicitly: *"we need to be able to
  queue"*; *"hermes cannot own all of it because it is just a plugin that will be optional"*;
  *"managing that is half the value of managing local vram for multiple model workflows"*;
  *"as smooth as possible of an experience for the hardware the user has."* This ADR records the
  decided architecture so implementation does not drift.
  Design council 2026-06-22: three competing architectures (A daemon wait-queue, B userspace lane
  library, C Hermes upstream queue) + four adversarial critics (resource-safety, determinism,
  market-differentiation, security). Verdict: **AgentOS owns the queue, in a SEPARATE arbiter
  process** — the safest realization of the user's steer (resource-safety scored it 9/10 vs 4/10
  for the in-daemon variant), and the one that protects the trust property that *is* the moat: a
  queue bug must never be able to freeze the desktop.
- **Amendment (2026-06-28) — resource-safety hardening pass EXECUTED.** A fan-out state review found no
  Accepted ADR awaiting execution, so we revisited the queueing strategy; a resource-safety lens surfaced
  four issues, all now BUILT + tested (231 tests, clippy clean) in `crates/agentosd/src/{lease,queue}.rs`:
  1. **§8 over-admit residual CLOSED.** A `Spawn` successor preempting a `Spawned` victim now confirm-frees
     the victim BEFORE launching the child — the spawn is DEFERRED past the off-lock reclaim, then installs
     under a re-taken lock (and aborts fail-safe if the lease was re-preempted meanwhile). This was the last
     reachable path where the safety layer itself could OOM the desktop.
  2. **A non-interactive WONT-FIT preempt now DENIES** instead of evict-then-OOM (`preempt_proceeds`): a
     Batch/BestEffort preempt that won't fit even after eviction leaves the victim running. `Interactive`
     stays fail-open (it must win; the gateway offloads).
  3. **Nomination is now authoritative.** The watcher keeps ONE nominee in flight, giving the
     highest-`(effective rank, seq)` head an EXCLUSIVE grace window before offering the next waiter, so two
     queued waiters can no longer race each other into a priority inversion (the old fixed-300 ms dwell
     could). Done WITHOUT coupling the daemon to arbiter state — the decoupling seam (the arbiter only reads
     `lease.json`; the daemon never reads the arbiter) is preserved; a daemon-honored nomination token was
     considered and REJECTED precisely because it would invert that dependency.
  4. **Bounded age-promotion is ON** (§2's escape hatch), because the deferral premise — "higher-tier
     demand is finite" — does NOT hold here: agents (≤Batch), dreams (Batch), and the overnight pipeline
     (Batch) all feed Batch, so BestEffort previews would starve. One rank per `AGE_PROMOTE_QUANTUM_MS`,
     quantized against a single `now`, capped strictly below `Interactive`, `seq` the final tiebreak.
  Owed: on-box e2e on the 4090 (a real Spawn-preempts-Spawn overlap; a starvation/aging demo).
- Date: 2026-06-22
- Deciders: Corey (binding product steer — see Status), design synthesis from three architecture
  proposals + four critic lenses (resource-safety, determinism-safety, market-differentiation,
  security). Pending: the Phase-1 GO/NO-GO review and on-box validation.
- **Supersedes (narrowly):** **ADR-0010** — the daemon's "there is no wait-queue; a losing acquirer
  is told `queued` and retries" stance (`lease.rs:29-32`, `coord.rs:18-23`). The *daemon* stays
  exactly that — a memoryless admit-or-reject arbiter + supervisor — but the system as a whole now
  HAS a wait-queue, in a separate AgentOS-owned process. The daemon's no-queue invariant is
  preserved; what changes is that "AgentOS does not queue" is no longer true at the product level.
- **Narrows (does NOT reopen):** **ADR-0019 §1/§6** and **ADR-0020 §2** ("any move toward ordering
  must supersede ADR-0019 explicitly, not smuggle it in"). This ADR is that explicit move. The
  narrowing: *no ordering of **intent** (ADR-0019's userspace deferral buffer stays FIFO-by-seq,
  non-prioritizing); a mechanical **(tier, wall-clock-age, arrival-seq)** ordering of the VRAM-demand
  wait-queue is permitted and is not prioritization-of-intent.* The `_FORBIDDEN_ORDER_KEYS` guard is
  carried into the arbiter as a CI-enforced invariant.
- **Relates to ADR-0013 §A1** (peer-cred socket) — **RESOLVED 2026-06-22, NOT a blocker.** The
  security critic flagged a multi-party queue on the bus as a flood/jump/cancel/enumerate surface and
  prescribed the A1 socket. On a closer look (and per ADR-0013's own line 106), a `0600` socket is
  marginal on a single-user box: the per-user session bus already excludes other users, a `0600`
  socket doesn't stop a same-uid confused-deputy either, and same-uid peers ARE distinguishable on the
  bus by their unique connection name — which the LIVE GO-2 binding (`holder_peer`/`may_release`,
  ADR-0021) already uses. The arbiter therefore reuses that per-connection identity for waiter-cancel
  authz and bounds flood by per-connection + global caps. The socket is declined for this single-user
  deployment (re-open only for multi-user/system-bus). See §5b.
- **Extends:** **ADR-0036** (the userspace lane library stays — it owns DURABLE "make-me-X"
  deferral; the arbiter owns LIVE cross-workflow ordering; they are complementary, see §4).
- Relates to: ADR-0001 (substrate not orchestrator — see §2, the intent-vs-resource line),
  ADR-0003 (fail-open supervised), ADR-0006 (Hermes plugin — now an OPTIONAL client, never the
  owner), ADR-0012 (keyhole — the `lease.json` mirror gains the `freed_seq` drain signal),
  ADR-0018 (graceful reclaim feeds admission), ADR-0021 (agent act verbs + tier clamp + identity
  binding — reused for waiters), ADR-0029 (UE wallpaper yields; a Yielding holder is a queue
  participant).

---

## 1. Context — the half of the product that wasn't built

A six-agent review of the live tree (2026-06-21/22) found: AgentOS *arbitrates* the GPU well (a
live, well-tested lease daemon does predict-before-load admission, tiered preemption, graceful
warm-pool/cgroup reclaim) but it **does not queue**. `do_acquire` is admit-or-reject: a loser gets
`busy_retry` + a flat 2000 ms hint that is explicitly "NOT a place-in-line," and **nothing drains
when VRAM frees** — every loser self-retries on its own timer. That was a deliberate ADR-0001/0010
choice that punted the queue to Hermes's gateway (built-but-not-installed) and to caller-side retry.

The user rejected that framing as under-built: **managing the contention queue is half the value of
managing local VRAM for multiple model workflows**, and Hermes — an optional plugin — *cannot* own
it (it is structurally blind to ComfyUI, Blender, and the UE wallpaper; it would only ever queue its
own inference). The market analysis confirmed the wedge is real and unserved: Ollama is blind to
ComfyUI, ComfyUI tells you to run models sequentially, MIG needs datacenter silicon, Run:ai/KAI need
a Kubernetes cluster — **nobody does cross-engine VRAM contention on a single consumer GPU where the
desktop itself is one of the contending workloads.** The smooth multi-workflow experience IS the
product; the queue is its visible tip.

## 2. The load-bearing distinction: intent vs resource demand

ADR-0001 ("substrate not orchestrator") is honored, not violated, because there are two different
queues and only one of them is Hermes's:

- **Queue of intent** — *what work to do*, dependencies, what-runs-tonight. This is Hermes's kanban.
  Re-deriving it would violate don't-reinvent.
- **Queue of VRAM-resource demand** — *who gets the GPU next when workflows contend*. This is the
  resource manager's job. A kernel run-queue and a memory allocator are **mechanism, not
  orchestration.** Owning it is the substrate doing its one job — and it is the value the user named.

The determinism critic ratified the line and made it exact: ordering by **clamped tier, then arrival
seq** — and *never* a caller-supplied weight/priority/urgency — is genuinely not intent-scheduling.
The clamped tier is the same already-disposed signal `arbitrate` uses (an agent can't author a
preempting tier, ADR-0021 GO-1); arrival seq is a deterministic FIFO tiebreak. The moment any caller
scalar enters the sort key, it becomes a worth-judgment = intent = Hermes's job. **The line holds
structurally, not by prose:** the arbiter's `Waiter` carries only `{tier, seq, est_mib, conn}` — no
ordered caller field exists — and a test proves selection is invariant to `est_mib`/`conn` (the Rust
analog of ADR-0019's `_FORBIDDEN_ORDER_KEYS` `SystemExit` guard).

**Ordering is `(effective tier, seq)`: strict tier, FIFO-by-seq, plus bounded age-promotion (built
2026-06-28 — see the amendment).** The two critics split: one proposed bounded wall-clock-age promotion
across bands (capped below Interactive) to stop a low tier starving behind a stream of higher arrivals;
determinism warned that cross-tier promotion edges toward intent and must stay quantized + single-snapshot.
v1 shipped *without* aging on the premise that higher-tier demand is finite on a single-user box — but the
resource-safety re-review showed that premise FAILS for this product: agents clamp to ≤Batch, dream
generation is Batch, and the overnight pipeline is Batch, so `Batch` is near-continuous and a `BestEffort`
preview starves behind it. So the documented escape hatch is now built exactly as fenced: one rank per
`AGE_PROMOTE_QUANTUM_MS`, **quantized against a single `now` snapshot** (stable within a quantum,
replayable), **intra-tier-then-band**, **capped strictly below `Interactive`** (aging never lets a queued
job seize the desktop/live-human GPU), with `seq` the final tiebreak. Age is mechanism, never a caller knob
— `selection_ignores_est_and_conn` still holds, and the only new input is monotonic wait time.

## 3. Decision — a layered architecture; the queue is a separate AgentOS process

```
 caller ── WaitTurn ─────────▶ [ agentosd queue ]      ← NEW: the cross-workflow
                                  transient, bounded       VRAM-demand wait-queue.
                                  (clamped tier, seq)      Proposes order. Holds NO
        ◀──── "your turn" ────────┘                        kill primitive, no lease.
                                                            Session bus (per-connection
                                                            identity, ADR-0041 §5b); polls
                                                            lease.json (freed_seq) to drain.
 caller ── Acquire / Spawn ──▶ [ lease daemon ]  ◀──── the SOLE admission gate.
                                SIGKILL / reclaim          Kill path UNCHANGED. Emits
                                admit (live NVML)          freed_seq on the free-edge.

 ► the CALLER (not the arbiter) races Acquire on "your turn" — its own identity → GO-2.
 ► a queue bug ⇒ at worst a batch job waits; the desktop CANNOT freeze.
 ► arbiter crash ⇒ degrades to today's direct-Acquire + busy_retry + self-retry.
```

**Layer 0 — the lease daemon (unchanged actuator + the SOLE VRAM gate).** Keeps its one job: lease
arbitration, SIGKILL/cgroup eviction, graceful reclaim, predict-before-load `admit` against live
NVML. It gains **no** wait-queue, no durable queue state, no new async on the kill path. It gains two
small things (Phase 0): the over-admit-window fix and a `freed_seq` emission. **`admit` remains the
single authority on whether VRAM is granted** — the arbiter never computes fit itself.

**Layer 1 — the arbiter (`agentosd queue`, a SEPARATE process — the new product value).** Holds the
bounded transient cross-workflow wait-queue; selects the next waiter deterministically by (tier, age,
seq); when the lease frees (it watches `lease.json`'s `freed_seq`, with a polling floor) it nominates
the best waiter and tells it "your turn." The nominated waiter then clears the daemon's real `admit`.
**The arbiter proposes order; the daemon disposes admission** — model-proposes/code-disposes applied
to the queue itself. It holds no lease, no PID, no kill primitive, so a bug in it can at worst make a
batch job wait; it can never freeze the desktop or over-admit VRAM (the daemon's `admit` is the
floor). If it crashes, every waiter falls back to direct `Acquire` → `busy_retry` → self-retry —
exactly today's behavior. This separation is the whole reason it's a separate process: it puts the
irreducibly-new, multi-party, durable-ish queue logic *outside* the process that holds the kill
switch.

**Layer 2 — the userspace lane library (ADR-0036, complementary, kept).** Owns DURABLE "make-me-X"
deferral (Lucid create-from-image, overnight batch) — intents whose originating context can vanish,
that survive crash/logout and drain headless. A draining lane registers with the arbiter like any
other workflow. The arbiter handles LIVE contention ordering; the lane library handles DURABLE
deferral. See §4.

**Hermes — an optional client, never the owner.** The `gpu-coordinator` plugin is fixed (per-call
tier classification, so batch inference queues as `Tier::Batch` instead of competing as
`interactive`) and becomes one more client of the daemon/arbiter. Because Hermes is optional, the
queue must work fully without it — which is exactly why the arbiter is AgentOS-owned.

## 4. Why the arbiter AND the lane library (they are different queues)

| | The arbiter (Layer 1) | The lane library (Layer 2, ADR-0036) |
|---|---|---|
| Queues | LIVE cross-workflow demand for the GPU *now* | DURABLE "make-me-X" intents |
| Lifetime | transient, in-memory, bounded | durable file-spool, survives crash/logout |
| Drains | on `freed_seq` (instant) + poll floor | headless drainer (a draining lane registers as a waiter) |
| Job | who gets the GPU next when workflows contend | hold-don't-drop an intent whose context vanished |
| Example | a video-gen fired while a dream renders + the wallpaper is up | a Dolphin right-click "create video from image" |

## 5. Safety prerequisites (both are hard Blockers, both fix real latent bugs)

**5a. Close the over-admit window (resource-safety Blocker; latent bug today).** On a preempt, the
**Spawned**-victim reclaim (`perform_reclaim` → `spawn_reclaim_task`) is fire-and-forget: it SIGKILLs
the victim and returns *before the VRAM is confirmed freed*, while the **Scope** arm
(`reclaim_scope`) already backpressures (polls the pinned fd until empty + settle + re-read). So a
preempt grant can return before the card is actually free → the successor allocates into not-yet-freed
VRAM → OOM, which on UE is a black wallpaper (the cardinal sin). Fix: give the Spawned reclaim the
same confirm-free backpressure (bounded `child.wait` + settle + free re-read) before `do_acquire`
returns the grant; fail-open on timeout (detach a reaper, proceed — the next admission's true-free
read governs). The arbiter further guarantees safety by never computing fit itself: a nominated
waiter always clears the daemon's `admit` against freshly-measured free VRAM.

**5b. Per-principal authz for the queue (RESOLVED via the session bus — NOT a new socket).** The
security critic flagged that a multi-party queue needs enforceable waiter-cancel authz, per-principal
flood caps, and queue-position no-leak, and prescribed the ADR-0013 §A1 peer-cred socket. On review
the socket is the wrong tool for a single-user box (see the "Relates to ADR-0013 §A1" header and
ADR-0013's own resolution note): the per-user session bus already excludes other users; a `0600`
socket does not stop a same-uid confused-deputy; and same-uid peers are distinguishable on the bus by
their unique connection name — exactly what the LIVE GO-2 `holder_peer`/`may_release` binding already
uses. So the arbiter rests on that **per-connection identity**: a waiter is bound to its acquiring
connection (only that connection may cancel it, reusing the `may_release` predicate), flood is bounded
by **per-connection + global** caps (per-uid == global on a single-user box, so a runaway can fill at
most its own slots up to the global ceiling), and queue-position replies are a number, never another
caller's identity (no-leak by construction). No client migration; no same-uid-porous filesystem spool.

**Documented residual (security review, MAJOR-downgraded-to-accepted):** the watcher decides "lease is
free" by a LEVEL read of the daemon's `lease.json` mirror, which is a same-uid-writable file. A same-uid
process could forge `{"tier":""}` to trigger a *spurious nomination* — but a nominated waiter still has
to clear the daemon's live-NVML `admit`, so a forged free at most wastes a nominee's `Acquire`
round-trip; it can never over-admit, preempt, or kill. This is the same trust class as a same-uid peer
calling `Acquire`/`Spawn` directly (already accepted by ADR-0013 on a single-user box), and strictly
weaker. Accepted, not closed; the `freed_seq` edge-trigger would reduce the churn but is declined for v1
because a pure edge-trigger breaks the common "idle lease + a new waiter arrives" liveness case (no
`freed_seq` advance occurs then). Likewise, **`freed_seq` is an OPTIMISTIC re-check hint, not a
confirmed-free signal** — it is bumped on the holder-none edge before a Spawned victim's async reap
completes, so no consumer may drive a destructive act off it; the daemon's `admit` is the only authority
(recorded on the `keyhole::Lease.freed_seq` field doc).

**Phase-1 GO/NO-GO outcome (2026-06-22):** the shell was BUILT (uncommitted) and the three-lens
re-review ran against the real code — resource-safety, security, and rust/async all returned
**GO-AFTER-FIX**, no Blocker, no NO-GO. The load-bearing assumption (zbus 5.16 dispatches concurrent
`&self` methods as detached tokio tasks, so a blocked `WaitTurn` never serializes others) was *verified*.
All MAJOR/MINOR findings were applied: poison-safe locking (no `.unwrap()` crash of the arbiter); the
watcher walks waiters highest-priority-first so a stuck/slow head can't starve the tail; a single lock
per access (no select-then-relock race); `spawn = true` pinned explicitly; the timeout read once via
`OnceLock`; and these doc fixes. 217 tests green, clippy clean.

## 6. Determinism + fail-open invariants (must hold by construction)

- **Selection is a pure function of `(effective rank, seq)`** — `effective rank` is the base tier plus
  bounded age-promotion (2026-06-28), computed from a single injected `now` so the whole selection is still
  deterministic and replayable from a log; `seq` is the total final tiebreak (never container/task-wake
  order). Cross-tier aging quantizes against that single `now` snapshot, never synthesizes `Interactive`,
  and keeps `seq` the final tiebreak — see §2.
- **Nomination is authoritative, not a race** — the watcher keeps ONE nominee in flight per free episode
  (an exclusive grace window before the next is offered), so the `(effective rank, seq)` head genuinely
  wins rather than losing a sub-second dwell race to a lower-priority waiter. The daemon's `admit` is still
  the sole disposer; the arbiter still never reads-back from the daemon beyond `lease.json` (decoupling).
- **The arbiter never grants.** It nominates; the daemon's `admit` against live NVML is the sole
  disposer of grant/deny/preempt/kill. No model output reaches selection or admission.
- **Every waiter has a hard deadline** (caller `timeout_ms`, daemon/arbiter clamped) — there is no
  unbounded park. On timeout the waiter returns `busy_retry` (the pre-queue world). The queue can
  only make things faster-or-equal-to-timeout, never slower.
- **Arbiter death frees everyone** — waiters fall back to direct `Acquire`/`busy_retry`/self-retry.
- **Blind NVML keeps the existing per-tier fail-open** — `Interactive` drains/grants, Batch/BestEffort
  hold/deny; never admit a heavy waiter blind.
- **Bounded + reject-newest** — per-principal caps (not per-bus-name); a full queue degrades to
  today's immediate `busy_retry`, never unbounded growth.

## 7. Phased plan + GO/NO-GO gates

- **Phase 0 (daemon prerequisites — needed for any locus, fixes real bugs now):**
  - 0a — close the over-admit window (Spawned reclaim confirm-free). [§5a] ✅ built + tested.
  - 0b — emit `freed_seq` in the `lease.json` mirror (additive `#[serde(default)]` field; bump on the
    lease→holder-none edge). [the drain signal] ✅ built + tested.
  - 0c — per-principal authz. **RESOLVED without new code: reuse the session bus's per-connection
    identity (GO-2 `holder_peer`/`may_release`) + per-connection/global caps. [§5b]** The ADR-0013 §A1
    socket is declined for the single-user box (re-open for multi-user/system-bus).
- **Phase 1 (the arbiter):** `agentosd queue` — bounded transient wait-queue, deterministic selection,
  poll-floor drain (freed_seq = future inotify optimization), per-connection + global caps, no-leak
  position. **✅ BUILT (uncommitted) + the GO/NO-GO 3-lens re-review PASSED (GO-AFTER-FIX, all fixes
  applied — see §5b outcome).** The pure core (`WaitQueue`) + the impure shell (`org.agentos.Queue1`:
  `WaitTurn`/`WaitTurnAgent`/`Status`, the `lease.json` poll watcher, the RAII `TicketGuard`) are in
  `crates/agentosd/src/queue.rs`, 10 tests. Deferred to Phase 2: an explicit `CancelWait` verb (v1
  cancellation is the `WaitTurn` call lifetime via `TicketGuard`); the `freed_seq` inotify optimization.
- **Phase 2 (wire demand sources):** the lucid drainer registers as a waiter; agent MCP `gpu_request`
  losers may opt to park (bounded); commit + harden the ADR-0036 lane library (size cap, TTL
  calibration, public idle-sweep).
- **Hermes plugin fix (independent, opt-in):** per-call tier classification so batch inference queues
  as `Tier::Batch`.

## 8. Consequences

- AgentOS visibly owns cross-workflow contention — the differentiator, demonstrable as the
  "one card, four jobs (UE wallpaper + LLM + video-gen + agents), zero freeze" run no incumbent can
  show. The trust line is structural: the queue lives outside the kill process, so a contention bug
  can never wedge the desktop.
- A new long-lived process (`agentosd queue`) + a new IPC surface (the peer-cred socket). Mitigated:
  it holds no destructive primitive and degrades to today's behavior on crash.
- The daemon stays small and as-hardened as today; its only new code is the two Phase-0 fixes, both of
  which also fix latent bugs.
- ~~A known residual to revisit: a *Spawn* successor preempting a *Spawned* victim spawns its child
  under the lock *before* the victim's reclaim confirms.~~ **FIXED 2026-06-28** (see the amendment): the
  preempt path now reclaim-confirms-then-spawns — the successor child is launched only after the victim's
  VRAM is confirmed freed, and the launch aborts fail-safe if the lease was re-preempted during the reclaim.
  This closes the last over-admit path the §5a/§8 fixes hadn't yet covered.

## 8b. Post-hardening adversarial review (2026-06-28)

The hardening pass was put through a multi-lens adversarial review (over-admit reorder, WONT-FIT deny,
async/race, determinism+aging+decoupling, test-adequacy), each finding independently verified
(real-vs-refuted, introduced-here-vs-pre-existing) plus a completeness critic — 30 findings, 24 confirmed,
1 must-fix. The confirmed-safe results worth recording: the deferred-spawn **held-but-ownerless window**
(lease installed, `inner.owned == None` for the ≤~3.15 s reclaim) is **safe** — supervise step-1 no-ops on
`owned==None`, B4 can't fire (`holder_peer=None` for Spawn), B5 TTL can't fire (≥90 s ≫ window), and the
wallpaper-restore stays suppressed (`holder_tier()` is `Some`).

**Also fixed in this pass (review-driven):** the must-fix — GPU-free `#[tokio::test]`s for the reorder
(reclaim-then-install happy path + spawn-fail rollback, seeding a real `Spawned` victim);
**`Spawn` clamped to ≤Batch** in the `spawn` verb (mirroring `adopt_scope` — an owned child can't CPU-offload,
so the Interactive fail-open exemption must not reach it); an honest **"VRAM unreadable, fails closed"**
deny message (no fabricated `short 0M`); **`position_of` made aging-aware** (no divergence from
`select_next`/`ordered`); one `now` read per watcher iteration; a `tier_rank`↔`Tier::Ord` consistency test;
a lazy `mirror_snap` on the spawn path; and a **startup warning when `AGENTOSD_QUEUE_WAIT_SECS` ≤ the
age-promote quantum** (aging would otherwise be silently inert). 234 tests, clippy clean.

**Documented follow-ups (not commit-blocking; all Low/nit, mostly pre-existing):**
- **The deny predicate inherits the undercount/offload blindspot.** `fits_after_evict` predicts post-evict
  free from the victim's *admitted estimate* (`holder_est`), not its actual resident VRAM; a `prev_est >
  actual-freed` skew could authorize a kill whose successor still won't fit. Bounded (reclaimable only for
  owned Spawn/Scope, which don't CPU-offload like Ollama; cooperative victims contribute 0) and strictly
  better than the pre-change no-fit-check preempt — but the predicate would be more honest reading resident
  VRAM. Revisit if per-process attribution lands (ADR-0048 Phase 3).
- **Aging tracks per-`WaitTurn` parked time, not total wait** — each re-enqueue restamps `enqueued_at_ms`, so
  a waiter that times out + re-parks restarts its age. At the default 30 s timeout a genuinely-starving
  waiter (blocked in one `WaitTurn`) still promotes at 20 s; the deeper cure is a per-conn first-seen stamp
  carried across re-enqueues (or a `waiting_since` token). The startup warning covers the sub-quantum-timeout
  footgun in the meantime.
- **Worst-case free-episode nomination walk ~12 s** (16 waiters × 750 ms grace) when *nothing* is admittable;
  during it the `queue.json` depth isn't republished and a mid-walk higher-tier arrival waits for the next
  episode. Common case is *more* responsive than the old 300 ms dwell (≤50 ms early break). Follow-up: cap
  the episode, publish depth inside the walk, re-snapshot `ordered(now)` when a grace window expires.
- **Spawn-fail rollback diverges from the other two release paths** (writes `Lease::default()`, doesn't bump
  `freed_seq`, leaves `holder_deadline`/`ttl`/`last_preempt` stale). Inert today (the arbiter reads `tier`,
  not `freed_seq`); route all three releases through one helper before any `freed_seq`-consuming arbiter —
  reversibility-tx-reviewer's lane.
- **`spawn_owned` runs a synchronous fork/exec under the tokio `Inner` lock** (pre-existing; the reorder made
  the window *shorter*). Move to `spawn_blocking` if exec latency ever bites. Same change widened the H3
  check-to-spawn TOCTOU window (a binary valid at pre-flight but deleted during the ~3 s reclaim → kill-for-
  nothing rollback) — bounded, rare.
- **Temporal ordering of the reorder** (child launched strictly *after* the victim's VRAM frees) is asserted
  by the owed **on-box e2e**, not the unit tests (which guard the path's outcome + rollback): successor PID
  start-time after the "post-evict free Δ reclaimed" log; no successor CUDA-OOM; `lease.json` never shows
  victim+successor co-resident; a concurrent Interactive Acquire during reclaim leaves zero orphans.

## 9. Alternatives considered (the three proposals, ranked by the panel)

- **A — in-daemon bounded wait-queue** (a `VecDeque<Waiter>` + `AcquireWait` verb inside `lease.rs`).
  Simplest, cleanest determinism, and security liked that the bus is the closable authz boundary
  (6/10 security). **Rejected as the primary** because it puts queue logic inside the SIGKILL process
  — resource-safety 4/10 ("a queue bug can wedge the safety floor"), market "fake moat" (throws away
  the trust property that is the moat). Its determinism rules and the `Waiter` shape are adopted by
  the arbiter; only the *locus* moved out of the kill process.
- **B — userspace lane library only + a `freed_seq` signal** (no cross-lane fairness; "the load
  doesn't exist"). Safest daemon delta (8/10 resource-safety) but **declines the user's mandate** —
  it explicitly provides no cross-workflow fairness, which is the contended-multi-workflow story that
  is the product. **Adopted as Layer 2** (durable deferral) but not as the answer to the steer; its
  `freed_seq` mechanism is adopted in Phase 0b.
- **C — Hermes upstream queue** (kanban owns it; daemon unchanged). **Rejected** — the user: Hermes is
  an optional plugin and cannot own it; structurally blind to ComfyUI/Blender/UE (market 3/10,
  "surrenders the wedge"); relocates a safety-critical busy-vs-dead liveness call into the plugin.
  **The one good idea kept:** fix the plugin's hardcoded `tier="interactive"` so Hermes batch
  inference queues correctly — as an optional *client*.

**Dissent recorded:** the determinism and security critics were comfortable with locus A (in-daemon);
the choice of a separate process trades a little added complexity (a new process + IPC) for the
trust property (the queue cannot freeze the desktop) that resource-safety and market judged decisive,
and that the user's "smoothest experience / never wedge the hardware" steer prioritizes. A1 is a hard
precondition for the arbiter regardless of locus.
