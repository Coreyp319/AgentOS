# Design-council brief — Keyhole condensed SYSTEM row: earned motion within symmetric tint

*design-discourse-mediator · 2026-06-16 · FINAL council brief. Combines the decided design
direction, the rating-panel verdict + 10/10 gap plan, the market positioning, and the delight &
differentiation wrap-up. Scope: presentation-only QML in `spikes/keyhole/` (throwaway, out of the
Cargo workspace). This is a refinement under the existing ADR-0012 §7 amendment — no behavior
contract changes; an ADR §7 guardrail sentence + an instrument-tokens update are the only doc
deltas a contributor must not skip.*

---

## 0. One-paragraph executive summary

The condensed SYSTEM row is the one **dead** surface on an otherwise **living** instrument — and
that is a *market* problem as much as a design one, because a static instrument advertises the
cheap-to-clone layer and hides the substrate that is the actual moat. The council converged on a
single direction: **motion is the off-nominal signal.** Healthy/transitional rows arrive then rest
still; only the attention row moves — as a **contained warm ember**, deliberately *quieter and
distinct* from the wallpaper's full `needs_you` dawn-bloom, because ADR-0012:153 reserves the warm
bloom grammar exclusively to `needs_you` and a SYSTEM `attention` (a failed/down/unreachable
daemon) is a *different predicate*. The signature beat is not the ember but the **recede**: when a
section heals to uniform-healthy, it *folds shut with a sunrise* — the instrument exhaling as the
system settles. Two pre-existing accessibility gaps (no keyboard path, colour-only health
assertion) must be fixed in the same diff. Current score: **7.72/10, ITERATE**, target 9.0, with
one armed cap that trips only if shipped code lands the warm wash without measured AA. The
delta-to-10 is BUILD (pin numbers, latch the tone-change, measure contrast + determinism), not a
redirection.

---

## 1. Decided design direction

**Headline:** *"The condensed row joins the aurora — motion is the off-nominal signal, the
attention row is a contained ember (not the `needs_you` bloom), and the fold is made honest in
words and keyboard."*

Three motion layers, all QtQuick-only, all reduced-motion→instant, all preserving the shape glyph
and the deterministic `boardPx`:

1. **Collapse/expand = opacity fade at a fixed height.** Member rows fade 0↔1 (~180ms); the
   disclosure caret rotates `▸→▾` (one rotated glyph). **No height tween** — animating the
   authoritative `boardPx` height risks the `contentHeight→0` popup-clip bug the deterministic
   design exists to prevent, and WCAG 2.3.3 names sliding panels a vestibular trigger, so the
   accessible answer and the safe answer coincide. Height-tween is **rejected, not deferred**.

2. **Tone-tint arrival sunrise.** When a section condenses or genuinely changes tone, the header
   tint `ColorAnimation`s in over 2500ms OutCubic (HorizonStrip's exact curve). Green *dawns into*
   green; healthy/transitional then **rest still**. Gated to a real tone transition — never every
   poll.

3. **Attention row = contained warm ember (the headline beat).** A translucent `#FF9957` wash
   behind the row: one-shot bloom-in on the transition into attention (1400ms OutCubic, the
   `AuroraRing` bloom curve), then a low-amplitude sustain breathing as a *binding* off a shared
   `_breath` driver. Text/glyph/bold stay contrast-locked on top. **Distinct from, and quieter
   than, the wallpaper's full `needs_you` dawn-breath** — honoring ADR-0012:153.

**Lands in the same change (non-negotiable blockers):**
- Keyboard operability of the toggle (`Keys.onReturnPressed/onSpacePressed → toggle()`) + a
  visible **shape** focus ring (survives on the warm wash) — the toggle is currently
  `MouseArea`-only (`FullRepresentation.qml:361-373`), a hard violation of the accessible
  non-negotiable.
- A per-tone count-bearing predicate + `Accessible.name` — a green-tinted "DREAMING" currently
  asserts health in colour alone; a screen reader hears "DREAMING, collapsed."

**Phase-2 (not a blocker):** extract the shared breath driver + `sectionWash` tokens into
`InstrumentPalette.qml`/`instrument-tokens.md`; add the guardrail sentence fencing audio/toast off
the `attention` tone.

---

## 2. Rating verdict + 10/10 gap plan

### Verdict: **ITERATE — 7.72 / 10** (target 9.0; no live cap, one armed)

| Dimension | Score | Weight | Contribution |
|---|---|---|---|
| Vision-fit | 9 | 0.30 | 2.70 |
| Experience | 7 | 0.30 | 2.10 |
| Craft | 8 | 0.18 | 1.44 |
| Feasibility | 9 | 0.12 | 1.08 |
| Market-fit | 4 | 0.10 | 0.40 |
| **Weighted** | | | **7.72 / 10** |

**Armed cap (CAP@5) — must travel with this work:** if shipped code lands the warm wash without
warm-text-over-settled-warm-wash AA passing in *both* registers (the `#FF9957` ~2.1:1
light-register risk, un-darkened at `InstrumentPalette.qml:34`), the overall caps at 5 regardless
of weighted average. It does **not** fire on the *direction*, because the direction makes the
measurement a binding gate. **Do not average it away.**

**Why market is a 4 and is *not* averaged up:** market-fit scores a different question — whether
this surface is the defensible wedge. It is not; it is polish on the most-copyable surface in the
repo, while the moat is the VRAM coordinator / tx / Hermes glue. The 4 correctly pulls the overall
down; the answer (G7) is substrate-blocked this round.

### 10/10 gap plan (prioritized, owned by makers, deduplicated)

- **G1 — Make the light-register contrast gate binding before the wash ships.** If warm-text-over-
  settled-warm-wash fails AA in the light register, keep `#FF9957` for the *wash* and drop the
  *text* to `skin.text`; dark register keeps warm text only if it measures ≥4.5:1. Focus ring is a
  shape outline, not a colour highlight. *Owner: `visual-systems-designer` + `ui-accessibility-reviewer`.*
  Closes Experience (the armed cap), Vision, Craft.
- **G2 — Mechanize the tone-change latch so the 8s poll cannot re-fire the bloom.** Carry
  `prevTone` per section in `_sectionMeta`/`_rebuildRows` (the model retains none across wholesale
  rebuild, `ServicesModel.qml:180-195`); gate the arrival sunrise and the ember bloom on a genuine
  tone *transition*. Add a test analogous to the shader's idle-stability test:
  "poll-with-no-change does not retrigger the section tween." *Owner: `design-technologist` +
  `interaction-designer`.* Closes Craft, Feasibility, Experience, Vision (the false-liveness bug
  all four design raters flagged).
- **G3 — Pin the ember tuple as concrete constants.** Resting wash opacity, bloom-in target
  (≤~0.14), sustain swing/amplitude (give the number, explicitly NOT "⅓ of the ring's 0.38 swing"),
  period (~6–7s, coprime-feeling vs the ring's 3200ms so they never phase-lock), easing (InOutSine),
  and the single reduced-motion still value (hold at the legible crest). Numbers, not `~`. Destined
  for `instrument-tokens.md` as `sectionWash` *derived from* `#FF9957`, never a literal. *Owner:
  `motion-designer` + `generative-artist` + `visual-systems-designer`.* Closes Craft, Vision;
  unblocks the two contingent reviewer sign-offs.
- **G4 — Land keyboard + predicate in the same diff.** Row focusable + Return/Space → `toggle()` +
  visible shape focus ring; per-tone count predicate reusing `summaryString()`'s "N need attention"
  *verbatim* (`ServicesModel.qml:97`) so words can't drift; set `Accessible.name` to the full
  sentence. *Owner: `interaction-designer` + `content-voice-designer`.* Closes the two surfaced
  non-negotiable violations.
- **G5 — Ship the at-rest pixel-diff proof, per state.** Checked-in fixed-state hash of the settled
  *static* layers (healthy/transitional headers + member rows) AND a settled all-attention hash
  proving the ≤0.14 sustain is the *only* moving pixel. Report popup-open Δframe-time. *Owner:
  `design-technologist`.* Closes Feasibility (the determinism gate — the one thing between 9 and 10).
- **G6 — Write the two ADR-coherence sentences.** ADR-0012 §7 guardrail (visual-only contained
  ember ≠ `needs_you` bloom, no sound/toast) + name the pre-existing static warm collision
  (`FullRepresentation.qml:306,314`) as deliberate bounded reuse + one sentence noting the
  symmetric-tint ceiling. *Owner: `content-voice-designer` drafts; human/code disposes into the ADR.*
- **G7 — [SUBSTRATE-BLOCKED] Bind the earned motion to the live VRAM-lease coordination event.**
  Bind the bloom/recede to lease acquire/evict, not daemon up/down — the only path that moves
  market-fit off "nice ambient touch." **Blocker:** the D-Bus lease push is "the remaining off-lock
  integration" (CLAUDE.md); the keyhole-facing producer is unbuilt. *Owner: `design-technologist`,
  gated on the producer.* Cannot close this round.
- **G8 — [ROADMAP] Define an `acting` row look before computer-use lands.** `ServicesModel.toneFor`
  (`:87-93`) has no `acting` tone. **Blocker:** `derive_feed` never emits state 3; the computer-use
  backend is unbuilt. Record in ADR-0012 §7. Not this round.

**Top 3 to close next:** G1 (disarms the only cap), G2 (closes the honesty bug all four raters
flagged), G3+G5 (turns Vision's and Feasibility's contingent 9s into ratifiable).

---

## 3. Market positioning (this surface, in the wedge)

**Position statement (the whole product, for context):** *For the single-prosumer-GPU local-AI
tinkerer who has hit — or fears — the desktop OOMing when a 19–21GB model loads into a GPU already
serving graphics, AgentOS is the local-first resource + safety floor under your AI (not an OS, not
an orchestrator) that coordinates one consumer GPU so inference never OOMs your machine — measured,
reversible, and it tells you why.* Category decision: **CREATE "local AI resource substrate"**;
refuse the crowded "agentic OS" shelf.

**This surface's role: Pillar 3 — the agent as a calm, honest environment.** The Keyhole is the
*visible face of the invisible substrate*. The market verdict and the design verdict are the **same
verdict**: a static instrument *looks* like the cheap-to-clone layer even though the moat (the
stale-gated, precedence-ranked, fail-open honest producer in `feed.rs`/`keyhole.rs`) is underneath.
Make the visible layer *earn* its motion, or it advertises the wrong story to exactly the technical
buyer the wedge targets.

**Honesty discipline carried into the motion:**
- The "zero footprint at rest" claim must stay literally true — green/healthy rows breathe **barely
  or not at all**; spend visible motion off-nominal even though tint is locked-symmetric.
- **"Honest UNKNOWN" is differentiation made felt:** when the panel is `unavailable`/stale, the
  condensed rows get **no** tint arrival, **no** ember, **no** breath — they go to the dim
  `skin.dim` still. No competitor's pretty dashboard declines to perform over stale data; this one
  does. (`ServicesModel.available`, `:61`.)
- **The demoable contrast (hand to `market-positioning-synthesizer` as the Pillar-3 clip):** a
  settled board sits dead-still and dim → one daemon fails → one row warms and breathes from below
  → the operator restarts it → the section *folds shut with a sunrise*. Motion appears only where
  the system actually changed. That clip *is* "calm at rest, warm only when it needs you" made
  literal.

**Market-fit delta-to-10 is BUILD, not message:** the message does not need a rewrite. The path off
4/10 for *this surface* is G7 (bind motion to live lease preemption) — substrate-blocked.

---

## 4. Delight & differentiation — the signature moves to land

**The ownable moment is the *recede*, not the ember.**

- **S1 — "It folded the silence" (the heal-and-condense beat) — SHIP THIS ONE IF ONLY ONE.** When a
  previously-mixed or attention-bearing section reaches uniform-healthy, the member rows fade out
  *into* the header, and the header's green *arrives last* via the 2500ms sunrise (the G2 latch
  makes it honest — fires only on a real transition). The felt experience: *the instrument exhaling
  as the system settles.* It is the keyhole's "revert feels safe" moment, and it costs one
  already-budgeted ColorAnimation. **Peak-end:** the emotional arc is *you saw something wrong → you
  fixed it → the instrument folded the worry away* — spend the most deliberate craft beat on that
  last clause.
- **S2 — The ember made *ours*, not a generic warm pulse.** (a) The bloom-in gathers **from below**
  the row (bottom-anchored gradient stop, mirroring the wallpaper dawn that always rises from the
  low edge); (b) the sustain breathes at **~6–7s** — deliberately slower than the ring's 3.2s,
  faster than the wallpaper's ~10s `needs_you` breath. *The ember breathes slower than the ring but
  faster than the wallpaper* — one organism at three scales.
- **Earned microdelights:** caret leads (rotate ~120ms), tint follows; the heal recede is ~1.3×
  slower than a user-clicked open (system settling is a sigh, user intent is crisp); healthy green
  *dawns in* at full chroma then settles one notch dimmer (`_settled` bound value) — the only honest
  way to partly repay the "colour spent on all-healthy" debt, recovered in the *time* domain since
  the colour domain is locked.

**Restraint cuts (explicit):** no sound/earcon on the heal beat (defer to `sound-designer`; audio
stays fenced to the feed's gated `needs_you`); no perpetual breathing on healthy rows; **stagger**
the member fade-out before the header dawn so the eye is handed from one to the next (sequencing
requirement, `motion-designer` owns the exact stagger, not free polish).

---

## 5. Accepted tradeoffs

- The attention row gets **less** life than the full aurora bloom several designers wanted —
  deliberately. Signature scarcity of `needs_you` outranks visual richness (honest-mapping
  non-negotiable).
- Healthy/transitional rows stay static; we forgo "everything breathes" to keep motion as the
  honest off-nominal channel.
- Symmetric-tint residual is **mitigated, not eliminated**: a resting all-healthy board is still
  multi-colour by the locked decision; predicate words + motion-asymmetry + dimmer-rest green are
  the best available recovery within the lock.
- Height tween rejected → disclosure is a fade, not a slide; some "drawer" physicality given up for
  boardPx + vestibular safety.
- Market-fit stays at 4 this round; the surface is knowingly not the moat, and the move that would
  change that (G7) is substrate-blocked.

---

## 6. Recorded dissent (never erased)

- **`motion-designer`, `generative-artist`, `visual-systems-designer`** lean toward giving the
  attention row the full perpetual breath grammar. Overruled on *grammar identity* by the
  honest-mapping non-negotiable (the contained ember is the resolution). They retain ownership of
  the ember's exact durations/amplitude/hue-terminus.
- **`content-voice-designer` / `sound-designer`** would require the attention motion be a *steady*
  bloom with **no** sustained breath, to maximally separate it from `needs_you`. The decision keeps
  a *low* sustain (distinguishable by amplitude + ~6–7s period). `ambient-embodiment-reviewer` is
  the named tie-breaker if "low sustain" still reads as impersonation.
- No dissent on the diagnosis, the asymmetric-motion mechanism, zero-GPU, or the boardPx contract.

---

## 7. Prioritized next actions to reach 10/10

1. **G1** — binding light-register contrast gate (wash-not-text if AA fails). *Disarms the only cap.*
2. **G2** — `prevTone` latch so the 8s poll cannot re-fire the bloom. *Closes the one honesty bug
   all four design raters flagged; prerequisite for any motion being honest-at-rest.*
3. **G3 + G5** — pin the ember tuple to numbers, then ship the at-rest pixel-diff + Δframe-time
   proof. *Discharges Vision's and Feasibility's contingent 9s; unblocks the
   `ambient-embodiment`/`responsible-ai-privacy-skeptic` sign-offs.*
4. **G4** — keyboard + predicate in the same diff. *Closes two pre-existing non-negotiable
   violations the motion work surfaced.*
5. **G6** — the two ADR-coherence sentences (the guardrail + the named pre-existing collision).
6. **Then re-rate.** Expected movement: Experience cap disarmed (G1), Craft/Feasibility honesty bug
   closed (G2), Vision/Feasibility contingencies discharged (G3+G5) → target ≥9.0.

---

## 8. Contingent sign-offs the 10-gate is explicitly waiting on

- `ui-accessibility-reviewer` — ratify warm-text-over-warm-wash AA in **both** registers
  (the `#FF9957` ~2.1:1 light-register risk, structurally mitigated because the signal now rides
  bloom+shape+bold+predicate, not warm text alone); confirm reduced-motion fully collapses every
  tween and the dim still meets AA.
- `ambient-embodiment-reviewer` — confirm the ember reads calm-not-alarm and the heal-fold reads as
  reassurance, not a glitch.
- `responsible-ai-privacy-skeptic` — confirm the contained ~6–7s breath provably does not
  impersonate `needs_you` (~10s).
- `design-technologist` / `rater-feasibility` — popup-open Δframe-time + the checked-in fixed-state
  hash (the residual-tween determinism gate).

---

## 9. Artifacts (proposed edits — code and the human dispose)

- `docs/design/keyhole-condensed-row-earned-motion.md` — **this brief.**
- `docs/adr/0012-keyhole-legibility-instrument.md` §7 — guardrail: the condensed SYSTEM `attention`
  row is a visual-only contained warm ember, **not** the `needs_you` bloom; triggers no sound/toast.
  Name the pre-existing static warm collision (`FullRepresentation.qml:306,314`) as deliberate
  bounded reuse. One sentence on the symmetric-tint calm-at-rest ceiling. (ADR stub below.)
- `integrations/design/instrument-tokens.md` — canonicalize `sectionWash` (derived from `#FF9957`,
  never a literal), the shared breath constant, the motion-reserved-for-attention rule, and the
  heal-beat / dimmer-rest-green constants.
- `spikes/keyhole/contents/ui/FullRepresentation.qml` (`:297-373`) — the three motion layers +
  keyboard path + predicate `Text` + `Accessible.name`.
- `spikes/keyhole/contents/ui/ServicesModel.qml` (`_sectionMeta` `:148-170`, `_rebuildRows`
  `:180-195`) — add `prevTone` carry + `predicateFor(tone,status,count)`.
- `spikes/keyhole/contents/ui/InstrumentPalette.qml` — `sectionWash` + shared breath/tween tokens
  (Phase-2).

---

## 10. Open questions for the human (options + recommendation)

1. **Ember sustain: low-breath vs. steady-bloom?** The room splits on whether the attention row may
   keep a *low* sustained breath (motion/generative/visual-systems) or must be steady-only to fully
   protect the `needs_you` signature (content/sound). **Recommendation:** ship low-sustain (~6–7s
   period, distinct from the ring), gated behind `ambient-embodiment-reviewer`'s calm-not-alarm +
   `responsible-ai-privacy-skeptic`'s impersonation sign-off; fall back to steady-bloom if either
   flags. Cost of low-sustain: marginal anxiety/impersonation risk. Cost of steady-only: slightly
   less alive. Reversible either way (one binding).
2. **Scope of the keyboard + predicate fixes (G4).** Pre-existing accessibility gaps the motion work
   surfaced but did not cause. **Recommendation:** fix them in the same change — shipping new motion
   on an unkeyboardable, colour-only-asserting row would harden two non-negotiable violations. Small,
   additive, revertible. Only cost is a slightly larger diff.
3. **Does the symmetric-tint lock get an escape hatch in the ADR?** The locked decision is honored
   throughout; the residual calm-at-rest cost is real and recovered only partially (in the time
   domain). **Recommendation:** keep the lock, record the ceiling in ADR-0012 §7 as a *known,
   bounded* tradeoff so a future contributor knows it was chosen, not missed — no behavior change,
   just an honest trail. Escalate to the product owner only if a re-rate shows Experience still
   capped by at-rest chroma after G1–G6 land.

---

## 11. ADR stub (for `docs/adr/`)

> **ADR-0012 §7 amendment (2026-06-16): condensed SYSTEM row earned motion — the contained ember.**
>
> *Status:* Proposed (presentation-only refinement; no behavior contract change).
>
> *Context:* The condensed SYSTEM row (ADR-0012 progressive disclosure) is flat/static on an
> otherwise living instrument. Within the locked symmetric-tint colour model, the row needs earned
> motion that belongs to the nimbus-aurora grammar without impersonating the wallpaper's reserved
> `needs_you` bloom (ADR-0012:153: "the reserved warm stays exclusive to `needs_you`"). A SYSTEM
> `attention` tone (failed/down/unreachable daemon) is a *different predicate* than the feed's
> `needs_you` (`aurora.frag:709`, gated on `uAgentWarm`).
>
> *Decision:* Motion is the off-nominal channel. Healthy/transitional rows arrive then rest still.
> The attention row carries a **contained warm ember** — one-shot bloom-in (1400ms OutCubic) + a
> low-amplitude ~6–7s sustain off a shared `_breath` binding, opacity capped ≤~0.14, **quieter and
> distinct** from the `needs_you` dawn-breath. Collapse/expand is an opacity fade at fixed
> `boardPx` (no height tween). The condensed `attention` row is a **visual-only redundant cue**:
> no earcon, no toast. All tweens collapse to instant under reduced-motion; the shape glyph and a
> count-bearing predicate (with `Accessible.name`) remain the never-colour-only carriers; the
> toggle becomes keyboard-operable.
>
> *Consequences:* The symmetric-tint lock keeps a resting all-healthy board chromatic (a known,
> bounded calm-at-rest cost, recovered partially via motion-asymmetry + dimmer-rest green +
> predicate words, not colour absence). Reversible (presentation-only QML, every tween one
> binding). Zero-GPU (QtQuick gradients/tweens only — §7 VRAM-coexistence reason holds). Honest
> UNKNOWN: an `unavailable`/stale panel performs no motion at all.
>
> *Gated on:* `ui-accessibility-reviewer` (both-register AA + reduced-motion), `ambient-embodiment-
> reviewer` (calm-not-alarm), `responsible-ai-privacy-skeptic` (no `needs_you` impersonation),
> `design-technologist`/`rater-feasibility` (popup-open Δframe-time + checked-in at-rest hash).
