# Design-Council Brief — Reviewable Request Queue (ADR-0019)

Status: **Decision-ready design; ITERATE to ship.** Mediator synthesis combining the decided
direction, the rating verdict (7.95/10 → target 9.0), the market position, and the delight pass.
Mediator: design-discourse-mediator. Date: 2026-06-16.
Pairs with: `docs/adr/0019-reviewable-request-queue.md` (Status: Proposed) ·
`docs/design/reviewable-request-queue.md` (decision-ready brief).

> **Mode declaration.** This scores and ships a **design proposal**, not merged code. ADR-0019
> is Proposed; v1 lives in `spikes/`; `lucid_jobs.py` is still a tmpfs tombstone board; the
> durable spool and drainer are unbuilt. "SHIP" below means "ship the *design*," never "the code
> exists." Every market pillar carries a maturity marker (PROVEN / DESIGNED / VISION) so the
> right delta gets scored.

---

## 1. The decision in one paragraph

Promote `lucid_jobs.py` from a tmpfs tombstone board to a **thin local durable deferral buffer**.
Deferral (couldn't-run-now → auto-run when the VRAM lease frees) stays **local and fail-open**,
riding the `AcquireResult::Queued` verdict the substrate already emits. Human-approval (B2
can't-verify, borderline consent) rides the **existing `needs_you.json` warm bloom** — no new
ambient mood, no new earcon. The queue is surfaced **read-only in the keyhole tray** (glance) and
**actionable in the lucid web panel** (act), with the **swaync recovery toast as the primary
control** where the drop is felt. **Private requests are ineligible for deferral and review** —
run-now-or-burn — enforced by one `enqueue(job_id)` chokepoint that hard-refuses `None`.
**Possible-minor stays a hard terminal block**: never enqueued, never warm, never given a review
affordance even disabled. The single load-bearing engineering move is **inverting source-of-truth**
(the spool file, not the detached process, is authoritative) — and that is what converts
preemption-loss into a recoverable held row.

---

## 2. Participants reconciled

Design team: `art-director`, `motion-designer`, `visual-systems-designer`, `interaction-designer`,
`design-technologist`, `generative-artist`, `sound-designer`, `brand-identity-designer`,
`content-voice-designer`, `design-researcher`.
Rating panel: `rater-vision-fit`, `rater-experience`, `rater-craft`, `rater-feasibility`,
`rater-market-fit`, aggregated.
Market: `market-landscape-analyst`, `market-differentiation-strategist`, `market-positioning-synthesizer`.
Reviewers to be consulted (named, not assumed): `responsible-ai-privacy-skeptic` (standing gate),
`ui-accessibility-reviewer`, `rust-performance-reviewer`, `determinism-safety-reviewer`,
`reversibility-tx-reviewer`, `wayland-computeruse-reviewer`, `ux-reviewer`, `ai-product-reviewer`,
`ambient-embodiment-reviewer`.

---

## 3. Decided direction (verified against code, not against ADRs)

The full state machine, edge ownership, label rename, motion spec, UX surface, and phasing live in
`docs/design/reviewable-request-queue.md` and `docs/adr/0019-reviewable-request-queue.md`. The
load-bearing decisions, each tied to the non-negotiable that settled it:

| Fork | Decision | Settled by (non-negotiable) |
|---|---|---|
| **Where it lives** | Local durable buffer (Option B) for deferral; reuse `needs_you` for approval; Hermes enqueue (Option A) deferred to Phase 3 | **fail-open** (ADR-0003): a Hermes-owned queue can't hold intent while Hermes is down — the exact silent-drop being fixed. Option A also needs a Hermes write-API that does not exist (ADR-0012 §6). |
| **Don't-reinvent guard** | The buffer has **exactly two exit transitions** (`held→running`, `held→needs-review/expired`) and **no priority/ordering field**. Any transition that decides *which* runs *first* = a scheduler → stop and escalate | **don't-reinvent** (ADR-0001): the buffer is the userspace wait-queue `lease.rs:29-32` deliberately declined to be — not a second orchestrator |
| **One surface or two** | Two terminal-distinct held states; shared storage, distinct chrome, distinct ambient channel | **honest mapping**: `held(deferred)` is calm `--st-idle` weather; only `needs-review` drives warm. visual-systems-designer's invariant adopted verbatim |
| **Private mode** | Structurally ineligible for defer/review; run-now-or-burn; one chokepoint refuses `None` | **local-first / consent** (ADR-0016) — escalated as a values fork (Open Q1) |
| **Possible-minor** | Terminal at `gate_seed` (`create_from_image.py:231`); no transition defined out; no affordance even disabled | **safety non-negotiable** — never reviewable |

**The warm-hue invariant (single most likely implementation mistake):** only `needs-review`
increments `needs_you.json` / drives the wallpaper warm bloom. `held(deferred)` surfaces *only* as
the keyhole count and NEVER touches warm — otherwise the scarce warmth is spent on "the GPU is
busy," a non-event, and the calm+honest mapping silently fails.

---

## 4. Rating verdict — **7.95/10 → ITERATE** (target 9.0)

Weights: Vision-fit 0.30 · Experience 0.25 · Craft 0.20 · Feasibility 0.15 · Market-fit 0.10.

| Dimension | Score | The one thing it needs |
|---|---|---|
| Vision-fit | **9** | Lock the anti-scheduler invariant in the **schema** (no priority field), not the prose |
| Experience | **8** | Specify recovery-toast a11y + **persistence-on-dismiss** — intent must survive toast timeout |
| Craft | **9** | Specify the drainer loop as pseudocode with single-flight; turn "invert source-of-truth" from slogan to spec |
| Feasibility | **7** | Spike the **non-Hermes-review-item → warm-bloom producer** (the cap-lifter) |
| Market-fit | **6** | Make preemption-reversibility the headline; prove the **closed loop** (lease-free event, not a polling drainer) |

**Why 7.95 and not 8.15.** No hard cap is live (don't-reinvent held; possible-minor airtight;
fail-open and reversibility are met-as-intent on a proposal). One 0.2 dock applied transparently
for **one root defect surfacing twice**: the design leans on `needs_you.json` for approval and on
`state==4`/`fleet.snagged` for stall earcons, and **neither channel has a producer path for a
non-Hermes (local lucid) item today** — making the "reuse, coin nothing" headline partly
aspirational. Docked once, not twice.

**Verified against code (the catch that fixes the headline):** `feed.rs:106` sources `fleet.snagged`
*exclusively* from the Hermes `kanban.db` SQL — a local lucid stall has no row there, so it can
never reach `state==4` today. And `feed.rs:86` reads `needs_you` as a bare count with **no notion
of origin**, while the Hermes `needs-you-signal` plugin does a whole-set `os.replace` (single-writer)
— a second lucid writer would race it. **Both "reuse" claims are unspiked new work, not wiring.**

**Two binding pre-Phase-2 gates** (not averageable polish): the recovery-toast a11y/persistence
contract (G5) and warm-state WCAG contrast under the bloom (G7). Without G5 the primary control
*is* the new silent-drop.

---

## 5. The 10/10 gap plan — prioritized, owned, deduplicated

Owners are makers. Each item names the rater-delta it closes. Substrate-blocked items are flagged.

| # | Gap | Owner | Closes | Priority |
|---|---|---|---|---|
| **G1** | Spike the **non-Hermes review-item → warm-bloom producer**: a lucid-owned `lucid_review.json` sidecar that `feed.rs` folds in **additively** (own liveness gate, no collision with the needs-you plugin's whole-set `os.replace`) | `design-technologist`, then `rust-performance-reviewer` | **Feasibility 7→9 (cap-lifter), Vision (reuse claim becomes true)** | **1 (highest leverage)** |
| **G3** | Specify the **drainer loop** as testable pseudocode: read spool → atomic `claim()` rename → acquire **`Tier::BestEffort`** lease (so it *structurally cannot* block `Tier::Interactive` — fail-open by construction, not by measurement) → run → writeback `done`/`held`+`attempts++` | `design-technologist` | Craft (slogan→spec), Feasibility (fail-open seam), Experience (dead-drainer honesty) | **2** |
| **G5** | **Recovery-toast a11y + persistence-on-dismiss** contract: keyboard-focusable actions, focus order, and the intent **survives toast timeout** (durable board is the backstop; "toast expired" = no-op on the held row) | `interaction-designer`, consult `ui-accessibility-reviewer` | Experience (delta 1 — **HARD GATE before Phase 2**) | **3** |
| G2 | Draft the **schema-2 keyhole JSON literal** + re-pin the round-trip test: `pending_requests` is almost certainly `{"held":2,"needs_review":1}` (two counts → two tray lines), not a singular int | `design-technologist`, `rust-performance-reviewer` | Craft + Feasibility (cross-producer drift) — one item, two deltas | 4 |
| G4 | Name the **retry policy as a pure function** with pinned tests: `next_state(attempts, last_error, age) -> held \| needs-review \| expired` — `derive_feed` treatment | `design-technologist`; `determinism-safety-reviewer` pins | Craft (untested decision surface) | 4 |
| G6 | **Lock the anti-scheduler invariant in the schema**: no priority/ordering field; strict-arrival-FIFO; any reorder halts and escalates. Test: two held items drain in arrival order, no code path reads a priority | `design-technologist` + `determinism-safety-reviewer` (merges with G4) | Vision (don't-reinvent intent→structure) | 4 |
| G7 | **Warm-state WCAG contrast** as a Phase-2 acceptance gate: AA for tray "needs your OK" text and web-panel Allow/Cancel/thumbnail measured *against the warm-bloomed field*, low brightness, on Hills **and** Flow | `ui-accessibility-reviewer` (gate), `visual-systems-designer` (tokens) | Experience (delta 2 — **HARD GATE before Phase 2**) | 4 |
| G8 | **Flag `state==4` snag reuse as not-yet-wired** *(substrate-blocked: `feed.rs:106` sources `fleet.snagged` only from Hermes `kanban.db`)*. Either route a local stall through G1's sidecar, or mark Phase-2 wiring honestly (the way `acting`/state-3 is flagged) | `design-technologist` | Craft (the one reuse-honesty slip), Vision (honest mapping) | folds into G1 |
| **G9** | **Ratify the private-mode values fork.** §5 is "Recommended," not "Decided." ADR-0019 stays Proposed until ratified — a **values ruling, not a maker fix**. No code lands on the private path before it | escalation → `responsible-ai-privacy-skeptic` via `design-discourse-mediator` | Vision + Feasibility confidence | **standing gate to ADR acceptance, regardless of score** |
| G10 | *(Phase 2, market)* Prove the **reversible-eviction closed loop**: a real lease-free *event* (the daemon push keyhole already wants) so auto-run is coordinated, not polled; + a named-comparator map | `design-technologist` (event) + `market-landscape-analyst` (map) | Market-fit 6→8 | Phase-2, not a v1 blocker |

**Two fixes free, do inline:** the private-mode ADR filename is `0016-lucid-private-ephemeral-mode.md`;
pin `needs_you.json` increment/decrement ownership so two producers can't double-count the bloom
(folds into G1+G2).

**Top 3 to close next:** **G1** (lifts the lowest gating dimension and makes the reuse claim true)
· **G3** (turns "invert source-of-truth" into a buildable spec with by-construction fail-open) ·
**G5** (the hard Phase-2 a11y gate — without it the primary control is the new silent-drop).
When G1+G2 land, re-rate: feasibility reaches 9 and the overall clears 9.0 as a **design ready to
build**.

---

## 6. Market position — "the resource-aware safety floor under your local creation queue"

**Position (one-liner, hand to `content-voice-designer` as the claim, not the copy):**
> *Your creation request is never silently dropped — it's held, retriable, and shown to you; and
> when you go private, it's deliberately, honestly forgotten.*

**Beachhead (both upstream lanes agree):** the local creative-gen power user already running
ComfyUI + a local LLM on a single prosumer GPU (4090-class, 24GB) — the literal user of the
Create-Video right-click who has *felt the exact bug*: right-click → GPU busy serving the
17–21GB model → request silently gone. The beachhead is defined by the **collision**, not a
privacy disposition.

**Category — REFUSE the crowded shelf.** Do **not** join "AI workflow orchestrator / HITL
platform" (Temporal, Inngest, n8n, Galileo): AgentOS structurally loses that comparison (they
have distributed durable execution; AgentOS has one box) and it violates ADR-0001. **Define
instead:** *the resource-aware safety + legibility floor under your local creation queue* — not a
queue product, the *floor* it stands on. Pay the explanation cost up front ("so it's not a
queue?"); win every defensibility argument after.

**Three pillars (each maturity-marked):**
1. **Never silently dropped, with a deliberate honest-ephemeral exception.** `[DESIGNED — bug
   PROVEN, fix is roadmap]` Durable-by-default + ephemeral-by-explicit-choice, both legible — a
   combination no mapped player holds (Outbox/DLQ are durable-only; incognito is ephemeral-only).
2. **The constitutional hard-refuse (possible-minor never enters review).** `[PROVEN]` A
   deterministic gate that hard-blocks *before* any queue path (`create_from_image.py:231-241`).
   A constitutionally un-reviewable class is a safety architecture no mapped player has — the rare
   case where a safety non-negotiable *is* the differentiator.
3. **Resource-aware deferral consuming a real lease verdict.** `[DESIGNED — verdict PROVEN,
   consumer is roadmap]` `AcquireResult::Queued` exists today (`lease.rs:29-32`); the moat is
   *underneath* (NVML pressure math, SIGKILL-on-preempt, monotonic tokens, anti-strobe dwell —
   built). **Honesty rider:** AgentOS SIGKILLs and re-runs from intent — it does NOT suspend-resume
   like Slurm. Never claim resume parity; re-run-from-durable-intent is the *correct* primitive.

**Market-fit scores:** Differentiation **8/10** (the wedge is genuinely uncontested — each
incumbent owns one primitive and is *structurally* barred from the others). Defensibility **7/10**
(the moat is the substrate underneath, not the panel on top; capped below 8 because the two most
differentiating pillars are roadmap).

**The single biggest score risk: the vaporware dock.** The headline verbs (hold, auto-run,
approve) are roadmap. Always position as a *roadmap promise on shipped substrate* with the
maturity markers attached — never present-tense. **Correctness table-stake to clear before any
"retriable" claim ships:** idempotent re-run (a re-submitted dream must dedup, not double-generate
— flag to `reversibility-tx-reviewer`).

---

## 7. Signature delight moves — "make the absence of a loss felt"

The defect this fixes is invisible-by-nature (a thing that *didn't* drop). Every move below rides
rails ADR-0019 already chose — **no new channel, no new mood, no new sound, and never spends the
scarce warmth.**

**S1 — "Nothing was lost": the held row that comes back to life *in place*. (the one-more-thing)**
When `held:deferred → running` fires on a lease-free event, the web-panel held row does **not**
vanish-and-reappear as a new card — the **same row** (bound to the spool `job_id`) warms from its
calm `--st-idle` slate into the dreaming aurora **in place**, reusing the existing `develop`
keyframe (`dreaming-moment.html:99`, 7s ease). The felt truth: *AgentOS kept my request exactly
where I left it, and it woke up on its own.* This is the demoable peak — the 10-second clip a user
films and sends to a friend ("watch — I never clicked twice"). It survives a `nimbus-flux` kill
mid-wait because the spool is source-of-truth: the row re-renders from the spool as a still, then
re-blooms. **The bloom is the *cool* dreaming aurora, not warmth** — this is the load-bearing
distinction (`visual-systems-designer` guards it).

**S2 — The empty queue that is byte-identical to calm.** Promote the §7 acceptance test (empty
queue → `iTime`-diff == 0) from a test to a *felt* signature. When the last held row drains, the
strip settles to the exact unmodified-idle field and the keyhole count eases to zero. The
calm-delight inverse of confetti: the reward is that *nothing remains* — it rewards the 100th
encounter precisely because it never accumulates residue. (`generative-artist`)

**Earned microdelights (calm, additive):** held row shows *"asked 6 min ago"* (patient, not late —
no countdown, no progress bar) · the seed-frame thumbnail *breathes faintly* at rest (dormant ≠
dead) — **held pending `motion-designer`'s ruling** against their recorded "no new animated
affordance" dissent; survives only inside the existing card · `Dismiss` leaves a **4-second undo**,
not a void (reversibility is the moat — dismiss should feel as safe as revert).

**Differentiation made felt:** the held line names the *true* cause honestly ("Waiting for the
graphics card") and sits beside the live `LEASE` block in the keyhole (`FullRepresentation.qml:127`)
— *no competitor's queue can name the lease.* The possible-minor refusal renders **no row at all**
(a calm wall, not an argument). The private intake line tells the cost up front in the same calm
voice: *"Private: sealed in RAM, runs now or not at all — never held, never shown."*

---

## 8. Recorded dissent (never erased)

- **design-researcher** dissents against any "ephemeral in-session private queue," on evidence
  (ADR-0016 residual seams + incognito precedent): choosing it knowingly widens a sealed seam. The
  mediator adopted research's position (ineligible-for-defer) as the recommendation — but it
  remains a values call for the human (Open Q1).
- **content-voice-designer** concurs (conditional): an in-session private hold is a surface, and a
  surface is the leak the no-board doctrine exists to prevent.
- **motion-designer** dissents against giving held requests a **new separate animated affordance**;
  held = a queued thing the keyhole already renders. (S1 is compatible — it reuses the *existing*
  card and the *existing* `develop` keyframe, adds no new pane; if motion-designer reads the
  thumbnail-breathe as a violation, it is cut.)
- **interaction-designer** anticipates **ux-reviewer** preferring one unified "Pending" list; held
  against, because list-simplicity costs signal-honesty, and honesty is the non-negotiable.
- **rater-feasibility / rater-market-fit (held, not averaged away):** the headline behaviors ride
  `lease.Queued`, which is told-to-retry, **not a wait-queue** — so v1's differentiated behavior is
  a *polling drainer*, shallower than the prose. Routed to G10 (closed-loop event) as a Phase-2
  differentiation item, not a v1 blocker.

---

## 9. Smallest shippable v1 vs phases

- **v1 (all in `spikes/`, no Rust, fully revertible):** promote `lucid_jobs.py` to a durable store
  under `~/.local/share/agentos/lucid-queue/` with `held`/`needs-review`/`expired` +
  `attempts`/`next_retry_after` + a snapshotted-PNG path; one `enqueue(job_id)` chokepoint refusing
  `None`; launcher writes `held` instead of `skipped` at `create_from_image.py:264`/`:309` and
  `lucid_linear.py:176`; a `--user`-timer **drainer** (single-flight, backoff, max-attempts →
  `expired`, crashed-`generating` → `held` recovery); the label rename; the private intake line.
- **Phase 2:** keyhole `pending_requests` read-only field (G2) + link-out; web-panel Held strip
  (S1 row-continuity); the `needs_you` warm-bloom bridge for review items (**blocked on G1**).
  *Hard a11y gates G5 + G7 must pass before this ships.*
- **Phase 3 (gated on a confirmed Hermes write-API, ADR-0012 §6):** mirror needs-review items into
  Hermes kanban; tray approve/retry/dismiss; Option A becomes feasible. Do not build on an
  unconfirmed API.
- **Phase 4 (reserve):** when computer-use lands, the `acting` ambient state and an in-flight veto
  attach here. **Open dependency, not a resolved design point:** `state 3 'acting'` is *defined but
  never emitted today* — consult `design-technologist` + `wayland-computeruse-reviewer`.

---

## 10. Prioritized next actions to reach 10/10

1. **G1 — spike the lucid-owned additive warm-bloom producer** (`lucid_review.json` → `feed.rs`
   additive fold). Lifts feasibility 7→9 and makes the reuse claim true. `design-technologist` →
   `rust-performance-reviewer`. *Highest leverage.*
2. **G3 — specify the drainer loop** with `Tier::BestEffort` fail-open by construction.
   `design-technologist`.
3. **G5 — recovery-toast persistence-on-dismiss + a11y** (hard Phase-2 gate). `interaction-designer`
   + `ui-accessibility-reviewer`.
4. **G2/G4/G6 — schema-2 literal, retry pure-function, anti-scheduler invariant in schema.**
   `design-technologist` + `determinism-safety-reviewer`.
5. **G7 — warm-state WCAG contrast gate** on Hills + Flow. `ui-accessibility-reviewer` +
   `visual-systems-designer`.
6. **Idempotent re-run proof** in `create_from_image.py` re-entry (clears the double-generate
   market risk; unblocks the "retriable" claim). `reversibility-tx-reviewer`.
7. **G10 — closed-loop lease-free event + comparator map** (Phase-2 market differentiation).
   `design-technologist` + `market-landscape-analyst`.

---

## 11. Open questions for the human (framed: options + cost + recommendation)

1. **Private-mode handling — the load-bearing values fork (G9, standing ADR gate).**
   *Recommend* **ineligible-for-defer** (airtight, research-backed: run-now-or-burn) over an
   *ephemeral in-session tmpfs queue* (more capable — private creations could auto-retry within the
   session — but widens the ADR-0016 seam and adds a second drainer). **Cost of the recommendation:
   private creations lose auto-retry; "never dropped" does not extend to private mode.**
   **`responsible-ai-privacy-skeptic` was not present this round and must ratify this — I did not
   rule the empty lane.** ADR-0019 stays Proposed until they do.
2. **Re-confirm on stale auto-run?** *Recommend* re-confirm a deferred *creation* past a TTL ("Still
   want this?"), run a deferred *check* silently. Cost: one extra tap on a forgotten creation vs a
   surprising artifact. Owner: `interaction-designer`.
3. **Phase-3 Hermes mirror** waits on a confirmed Hermes write-API (ADR-0012 §6). *Recommend* not
   building on an unconfirmed API — hold Phase 3.

**Mediator flag carried into the ADR, not resolved by the room:** `state 3 'acting'` is defined but
never emitted today, so Phase-4 computer-use attachment is an open dependency (consult
`design-technologist` + `wayland-computeruse-reviewer`), not a resolved design point.

---

## 12. ADR status

No new ADR stub is implied — **ADR-0019 (Status: Proposed)** already captures this behavior change
and is the artifact the human disposes on (`docs/adr/0019-reviewable-request-queue.md`). It must
**stay Proposed** until **G9 (private-mode ratification)** lands, regardless of rating score. The
gap-plan items refine the *content* of ADR-0019 (schema-2 literal, drainer pseudocode, anti-scheduler
schema invariant, a11y gates, the G8 `state==4` not-yet-wired honesty flag); they do not require a
new ADR. A second ADR becomes warranted only if **Open Q2** resolves toward a TTL-reconfirm behavior
or **Phase 3** introduces a Hermes write-path — both are new behavior changes that earn their own ADR
when chosen.
```
