# Scorecard — ADR-0019 Reviewable Request Queue for Local Creation Intents

Synthesis by the rating panel (panel-synthesizer). Mode: **DESIGN PROPOSAL** (ADR-0019 is
*Proposed*; the only shipped code on this work is the G2 keyhole `pending_requests` schema-2
mirror — everything load-bearing for the wedge, G1/G8/G10, is unbuilt). A verdict here ships the
*design*, never implies the code exists. Weights tilted to vision/experience per the proposal mode.

Weights: vision-fit 0.30 · experience 0.25 · craft 0.20 · feasibility 0.15 · market-fit 0.10.

## Round 2 — Overall: 8.80 / 10 → ITERATE (below the 9.0 bar)

Weighted math (no cap fires; uncapped == capped):

| Dimension     | Weight | Score | Contribution |
|---------------|--------|-------|--------------|
| vision-fit    | 0.30   | 9     | 2.70 |
| experience    | 0.25   | 9     | 2.25 |
| craft         | 0.20   | 9     | 1.80 |
| feasibility   | 0.15   | 9     | 1.35 |
| market-fit    | 0.10   | 7     | 0.70 |
| **Overall**   |        |       | **8.80** |

**Caps: none fired.** The a11y cap is ARMED but NOT triggered — warm-on-warm contrast (G7) and
reduced-motion (§8) are correctly gated *before* Phase 2 rather than shipped-failing
(`rater-experience` cap_reason). The reversibility, fail-open (Tier::BestEffort by construction),
don't-reinvent (`_FORBIDDEN_ORDER_KEYS` + SystemExit fail-closed + strict-arrival FIFO), and
possible-minor-stays-terminal caps all hold and several tightened vs round 1. So
uncapped 8.80 == capped 8.80. **Standing warning (carry into round 3):** if G5 (recovery-toast
a11y/persistence) or G7 (warm-state WCAG contrast) ships UNMET, the a11y cap fires and experience
drops to <=5, which would re-cap the overall regardless of the weighted average.

## Per-dimension — the one thing each needs

- **vision-fit 9** — Close G8 *or* demote the claim: route a local lucid stall through G1's
  review.json sidecar so `state==4` can fire for a non-Hermes stall, OR write "Phase 2" on the
  state==4 reuse line in every artifact incl. delight/market copy. "Reuse, coin nothing" has one
  aspirational limb until then. (Verified: `feed.rs:106` sources `snagged` only from kanban.db SQL.)
- **experience 9** — Get G5 and G7 GREEN IN CI, not specified. `derive_feed` is still the two-arg
  `needs_you` bloom (`feed.rs:78`); the recovery toast, drainer, and held web-panel row are unbuilt,
  so every legibility guarantee is a hard gate the code does not yet pass.
- **craft 9** — Land G4/G6 (`next_state`/`drain_order`/`retry_backoff_s`) as pure fns with the
  enumerated precedence tests, to the `derive_feed` 11-running-unit-test bar — they exist only as
  decision-ready pseudocode today. (G2 keyhole `pending_requests` is exemplary and verified built:
  `keyhole.rs:89,373,538`, byte-exact round-trip re-pinned, 62 tests pass.)
- **feasibility 9** — Build the load-bearing unbuilts round 2 specified: the lucid review.json
  sidecar + the 4-arg `derive_feed` additive fold, the ExecStop multi-session burn fix
  (`ST.list_priv_queue()` does not exist; only `list_private` at `lucid_store.py:265`), and the
  durable `lucid_drain.py` flock single-flight. A short design-technologist spike proving
  review.json → feed.rs warm bloom end-to-end closes the last unspiked seam.
- **market-fit 7** — Close G10: replace the ~20s POLLING drainer with the lease-free EVENT (the
  daemon push the keyhole already wants), so "auto-run the moment the GPU frees" is coordinated
  coordination no hand-rolled cron-retry can copy — instead of prose riding `lease.Queued`
  (told-to-retry, NOT a wait-queue). This is the lone sub-9 score and the verdict-deciding gap.

## Disagreements — dispersion and adjudication

Four 9s and one 7. Variance is low (range 2) but the split is **not noise** — `rater-market-fit` is
the lone dissenter and its delta is the one that keeps the overall below 9.0. Do not average it away.

**The hidden axis is best-effort/polled vs guaranteed/event-driven (domain pitfall #8c).** The four
9-raters score the *contract* — the schema, the state machine, the cap discipline, the reversibility,
the ADR trail — and that contract is genuinely 9-grade and code-verified where it ships (G2).
`rater-market-fit` scores the *moat*, and the moat rides the one part that is still polled, not the
closed loop: ADR-0010's `lease.Queued` is "told to retry," not a wait-queue, so today's reactivity is
"the desktop polls," not "the desktop reacts." That is a real, legitimate divergence — an
"enforcement/legibility" score and a "differentiation/coordination" score *should* diverge while the
event seam (G10) is deferred. **Adjudication: the 7 is correct and the gap it names (G10) is the
single highest-leverage move toward 9.** It is not a craft defect the makers can polish around; it is
a substrate-blocked event that must be landed.

Second, smaller axis: all five raters touch the **same root unbuilt** (G1 — `derive_feed` has no
`lucid_review` param, verified at `feed.rs:78`). Vision, experience, feasibility, and market-fit each
name it. **Deduped to ONE gap below with one owner**, listing all four deltas it closes — do not let
it appear four times and inflate the plan (pitfall: double-counting the same `feed.rs` anchor).

## 10/10 gap plan — prioritized, owned, deduplicated

Ordered by leverage. Owners are makers (never raters). Substrate-blocked items are marked with their
blocker. **"Build" items advance the design toward shippable code; this remains a proposal verdict.**

1. **G1 — fold the lucid review.json sidecar into a 4-arg `derive_feed` (the cap-lifter + the moat
   producer).** Build the lucid-owned `review.json` producer + a `read_lucid_review` reader in
   `feed.rs`, and the additive 4-arg `derive_feed` fold with its own ~12s liveness gate
   (bit-identical at `lucid_review=0`, schema-1 consumers ignore the trailing field). This single
   item closes the **vision** "reuse/coin-nothing" limb, the **experience** warm-bloom guarantee, the
   **feasibility** last-unspiked-seam, and the **market-fit** "closed-loop moat is vapor on the
   producer side" delta. Owner: **design-technologist** (the review.json → feed.rs warm-bloom spike,
   mirroring proven hills-reactive), then the maker landing the Rust fold. Verified unbuilt:
   `feed.rs:78` is still `(fleet, gw, needs_you)`.

2. **G10 — replace the ~20s polling drainer with the lease-free EVENT (the moat).** SUBSTRATE-BLOCKED
   on the daemon push (the same off-lock integration the keyhole's `lease.json` mirror still wants;
   CLAUDE.md keyhole note). Lifts **market-fit 7→~9** and removes the "desktop polls" honesty rider
   from every reactivity claim (**vision**). Owner: **design-technologist** for the event design; the
   daemon-push landing is the blocker to clear first — keep the polling-latency honesty rider on
   every reactivity claim until it lands. Consult **wayland-computeruse-reviewer** is NOT needed here
   (no coordinate path); this is the D-Bus/lease event seam.

3. **Condition 1 — multi-session ExecStop private burn.** `_burn_private_on_stop()` burns only the
   hardcoded `SESSION='web'` (`lucid_web.py:40,687-688`); `ST.list_priv_queue()` does not exist (only
   `list_private` at `lucid_store.py:265`). Until fixed, the intake copy's "gone the moment you log
   out" is FALSE — a privacy-acceptance gate, not polish. Lifts **experience** and **feasibility**;
   it is one of the Conditions 1-3 behind which NO private-path code lands. Owner:
   **design-technologist** (Python). Consult **security-reviewer** on the burn completeness.

4. **G4/G6 — land `next_state`/`drain_order`/`retry_backoff_s` as pure fns with enumerated tests.**
   Bring the decision surface to the `derive_feed` 11-running-test bar (total-order precedence,
   `created`-is-not-the-sort-key clock-skew defense, backoff-orthogonality, `_FORBIDDEN_ORDER_KEYS`→
   SystemExit), plus `lucid_drain.py` flock single-flight `claim()`. Lifts **craft 9→10**. Owner:
   **design-technologist**.

5. **G5 + G7 — recovery-toast a11y/persistence and warm-on-warm WCAG contrast, GREEN IN CI.**
   These are the ARMED a11y cap. Lifts **experience 9→10** and keeps the cap from firing in round 3.
   Owners: **interaction-designer** (recovery-toast persistence + the dead-drainer "I can't see the
   queue" third reading), **visual-systems-designer** (warm-on-warm contrast on Hills+Flow; F2
   de-couples warm from text fill). Consult **ui-accessibility-reviewer** to sign the CI gate.

6. **Copy/ADR-discipline tidy — scrub residual stale council-brief lines** (§7 delight line 209
   "runs now or not at all", §11 Open-Q1) so all three artifacts agree the private-values fork is
   RESOLVED and the old dishonest intake line is forbidden. Refines ADR-0019 content; **no new ADR**
   (a new ADR is correctly reserved only for Open-Q2 TTL-reconfirm or the Phase-3 Hermes write-path).
   Lifts **vision**. Owner: **content-voice-designer**.

**Standing gap (touches both sides of the producer/consumer boundary):** the `agent.json` contract
`{"state":N,"busy":f,"warm":f,"snag":f}` is pinned only by a serde round-trip test (`feed.rs`), with
no JSON Schema and no versioning. G1 widens `derive_feed` and keyhole schema-2 already added a
trailing field — surface "no versioned contract across producer/consumer" as a standing risk while
this work touches either side. Deduped to one line; closes a **craft + feasibility** delta. Owner:
**design-technologist**.

## Verdict — ITERATE (8.80 < 9.0; no cap)

Ship the *design* direction — the contract, state machine, cap discipline, and reversibility are
9-grade and the override-with-dissent is recorded honestly. But the work is not at the 9.0 bar and
the moat is still on the roadmap, not in code. **Next 3 moves:**

1. **G1** — land the review.json sidecar + 4-arg `derive_feed` fold (closes 4 of 5 deltas; the
   cap-lifter and the moat producer in one).
2. **G10** — clear the daemon-push blocker and make auto-run event-driven, not polled (lifts the lone
   sub-9 market score; removes the "desktop polls" honesty rider).
3. **Condition 1** — multi-session ExecStop burn, so the privacy promise stops being FALSE-as-written.

Hold G5/G7 as the armed a11y cap to watch: do not ship either unmet or the overall re-caps.

## Round delta (round 1 → round 2)

| Dimension     | R1 | R2 | Δ    | Note |
|---------------|----|----|------|------|
| vision-fit    | —  | 9  | —    | Soft tensions closed/flagged; one aspirational limb (G8) remains, honestly flagged so no cap. |
| experience    | —  | 9  | —    | Legibility guarantees still hard gates the code doesn't pass; a11y cap armed-not-triggered. |
| craft         | —  | 9  | —    | G2 keyhole schema-2 ships exemplary (62 tests); G4/G6 still pseudocode vs the running-test bar. |
| feasibility   | —  | 9  | —    | G1/private-path unbuilts now a precise diff; no async shift, no new runtime. |
| market-fit    | —  | 7  | —    | The lone sub-9; G10 closed loop still polled-not-event; G1 producer still vapor. |
| **Overall**   | 7.95 | 8.80 | **+0.85** | ITERATE → ITERATE. Closer to the bar; held below 9.0 by the market-fit 7 (G10) and the unbuilt G1 producer. |

Round 1 reported only a 7.95 aggregate (per-dimension R1 scores were not supplied to this synthesis;
the +0.85 movement is the overall delta). Progress toward 10 is real and legible: the contract
hardened (don't-reinvent now structurally enforced via `_FORBIDDEN_ORDER_KEYS`/SystemExit, warm-hue
invariant locked into the schema field name), and G2 moved from spec to verified-built code. The two
things standing between 8.80 and 9.0+ are both producer-side: **G1 (build the warm-bloom loop)** and
**G10 (make it event-driven, not polled)**. Close those and the wedge becomes a shipped moat, not a
roadmap promise.
