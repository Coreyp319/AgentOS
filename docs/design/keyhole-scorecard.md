# Keyhole — Panel Synthesis Scorecard (Round 1)

**Mode: DESIGN PROPOSAL** (candidate ADR-0012; new read-only `keyhole.json` producer
+ greenfield Plasma applet — no implementing code yet). Verdict scores the *design*,
not merged code. Target bar = 9.0.

## Weights (adjusted, with reason)
Vision-fit **0.32** · Experience **0.28** · Feasibility **0.16** · Craft **0.14** · Market-fit **0.10**.
Calm-honesty surface → vision + experience lead. Feasibility nudged up (greenfield
plasmoid is the dominant unknown — it gates). Craft slightly down (data layer is a
near-clone of proven `feed.rs`).

## Overall (uncapped weighted): **8.1 / 10**
`9(.32)+8(.28)+7(.16)+8(.14)+7(.10) = 2.88+2.24+1.12+1.12+0.70 = 8.06`
**No cap is live.** Vision-fit found no non-negotiable violation (board=link-out honors
don't-reinvent; v1 read-only keeps reversibility intact). Experience a11y is correct-by-
construction (not failed). So **final = 8.1** (no penalty applied).

## Per-dimension — the one thing each needs
- **Vision-fit 9** — ADR-0012 must bind the horizon strip to the shader's never-red snag law.
- **Experience 8** — measured WCAG-AA contrast number per state (snag first), not asserted.
- **Feasibility 7** — a design-technologist Plasma-6 tray-plasmoid spike (the dominant unknown).
- **Craft 8** — a `keyhole.json` contract test + a **versioned schema field**.
- **Market-fit 7** — LEAD with arbitration (who holds the GPU + the preempt event); demote gauges.

## Dispersion — the 9→7 spread is ONE lens, not real disagreement
Variance traces to the **proposal-vs-code axis**, not conflicting taste. Vision/experience
score the *idea* (strong); feasibility/market score *what exists* (a data clone + an
unbuilt applet). Adjudication via the spike ledger: the data seam is a near-clone of
proven `feed.rs` + `monitor` → feasibility's "data layer" worry is already spiked away
(floor lifted). The *live* unspiked risk is the **greenfield plasmoid + the Rust→QML
file-poll seam** — that 7 is honest, not pessimism. Do NOT average it away; it is the
single thing that converts this proposal to a buildable brief. Market's 7 is a *framing*
gap (moat shown-not-led), addressable in-direction now — it is not a capability ceiling.

## 10/10 Gap Plan — consolidated, deduped, owned, prioritized

### A. DESIGN deltas — fold into ADR-0012 + brief NOW
1. **Snag-contrast PROOF** — measured WCAG-AA per state, snag first, against quiet-dark +
   dimmed-snag. `visual-systems-designer` (+ `ui-accessibility-reviewer`). Closes EXP-1, lifts EXP→9.
2. **Ship the `acting` row** (state 3: text+icon+dot), reserved-but-visible for when
   computer-use lands. `interaction-designer` + `content-voice-designer`. Closes EXP-2.
   *Note: `derive_feed` never emits 3 (feed.rs:78-96); ship the LOOK now, data is B/C-blocked.*
3. **UNKNOWN precedence + politeness** — ADR states UNKNOWN vs idle/snag ordering; UNKNOWN is
   polite-not-anxious when Hermes is merely restarting (degraded). `content-voice-designer`
   + `interaction-designer`. Closes VIS-3 + EXP-4. (The UNKNOWN honesty win fixes feed.rs:210.)
4. **Horizon strip = never-red snag law** bound in ADR-0012. `generative-artist`/`motion-designer`.
   Closes VIS-1.
5. **Reduced-motion clamps the TWEENS, not just the dot.** `motion-designer`. Closes EXP-3.
6. **Frame-time / power budget** named for keyhole.json cadence + strip render; state
   adaptive-cadence bounds vs the 2s poll. `design-technologist`. Closes VIS-2 + CRAFT-4.
7. **LEAD with arbitration** — narrative + layout foregrounds lease holder/tier + preempt
   event (wallpaper yielded ~1.5GB, model loaded), demotes commodity gauges.
   `visual-systems-designer` + `interaction-designer`. Closes MKT-1 + MKT-3.

### B. SPIKE / BUILD deltas — sequence after the ADR
8. **Plasma-6 tray-plasmoid spike** — minimal applet polling a static keyhole.json on a
   **Timer** (hills-reactive: QML6 sync-XHR leaves values stale), proving the 3 reps + blur
   + file seam. `design-technologist`. Closes FEAS-1 + FEAS-2 (the dominant unknown).
9. **QML density harness** — prove the tween+dot frame budget at max load ("property-animation-
   only, no Canvas cost" is currently UNPROVEN). `design-technologist`. Closes CRAFT-3.
10. **keyhole.json schema + contract test** (ONE item — craft & feasibility double-counted) —
    serde round-trip on an exact string **+ a versioned schema field** to close producer→QML
    drift. `design-technologist`. Closes CRAFT-1 + FEAS (no-versioned-contract standing gap).
11. **Push-off-lock** — lease→keyhole.json write is fire-and-forget OFF the Mutex (reuse
    `spawn_reclaim_probe`, lease.rs:290); never `fs::write` while holding `Inner` or a render
    delays a SIGKILL. `design-technologist` (+ `resource-safety-reviewer`). Closes FEAS-3.

### C. DOWNSTREAM-DEPENDENCY deltas — mark blocked, do NOT promise this round
12. **RT-yield horizon-strip tell** — rides a signal that DOESN'T EXIST. Either add `rt_yielded`
    to the feed contract (ADR-0004) **or** render the strip UNKNOWN until the coordinator emits it.
    *Blocker: no producer.* Prefer the UNKNOWN fallback in v1. Closes CRAFT-2.
13. **tokens/sec live** — vapor until the ADR-0002 proxy emits it. Ground it or DROP from v1.
    *Blocker: proxy unbuilt.* Closes MKT-4.
14. **approve/pause** — gated on a CONFIRMED Hermes write-API (code disposes). *Blocker: P2.*
15. **revert / close-the-loop-to-action** — needs the ADR-0005 apply/rollback tx. *Blocker: P3.*

## Verdict: **ITERATE → then COMMIT** (8.1 vs 9.0 bar)

The DIRECTION is sound and uncapped — commit it to a candidate ADR-0012 + brief **with all
of section A folded in**, and B/C explicitly sequenced (B as the de-risking spikes, C marked
substrate-blocked). But do not call it 9.0 yet: two A-items (snag-contrast proof, acting row)
are flagged as **hard constraints, not averageable**, and the dominant feasibility unknown
(greenfield plasmoid, item 8) is unspiked. One tight design round closes A; the 9.0 SHIP comes
when A is in the ADR and item 8 returns VIABLE.

### Next 3 moves
1. **Snag-contrast measured proof** (A1) — `visual-systems-designer` + `ui-accessibility-reviewer`.
2. **Plasma-6 plasmoid spike** (B8) — `design-technologist`; resolves the 7→9 feasibility gap.
3. **Fold acting-row + UNKNOWN precedence + lead-with-arbitration into ADR-0012** (A2/A3/A7).

*Round 1 — no prior round to delta against.*
