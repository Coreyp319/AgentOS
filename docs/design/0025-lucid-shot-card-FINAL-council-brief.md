# Lucid "Shot Card" — FINAL design-council brief

**Annotations-as-prompt fuse-review · ADR-0025 amendment · 2026-06-21**
*Synthesized by `design-discourse-mediator` from the design discourse (mean 8.3), the rating-panel
verdict (7.6, ITERATE), the triangulated market positioning, and the delight & differentiation pass.
Status: ITERATE — ship the direction, gated on one named edit-round. UNCOMMITTED, spike-grade.*

---

## 0. What this is (and is not)

The **Shot Card** makes a dream's four hidden prompt-authors — your typed line, the VLM
`decompose_notes` rewrite, the persistent subject prefix, and the relocated hold-anchor — into a
**visible, editable, gated** readback ("how Lucid reads this") *before* the user commits GPU minutes.
With zero notes it is byte-for-byte the old one-tap compose; with notes it grows into the card,
shows the exact string the next beat will run, and lets you edit it. `step(fused_edited=)` runs that
reviewed text verbatim (skips re-decompose AND subject re-prefix → what-you-saw-is-what-runs) while
the LTX pixel guides + hold-anchor still derive from the notes (a text edit never disables a mask).

**Lane:** this is a *Dreaming/Lucid surface* (ADR-0025) — a thing the substrate **enables**, not a
substrate primitive. That distinction is load-bearing for the market read (§3) and is the whole of
the escalated strategic question (§7). It does not lower the design bar; it bounds the market claim.

**Verification posture:** the string-assembly, fuse, gate, and fast-path code exists and is unit-
tested (full backend suite green incl. `test_lucid_linear` 39 + `test_lucid_web_fuse` 4; vite build +
eslint clean; tsc only pre-existing errors). The **headline behavior — an edited reading actually
rendering to screen — is GPU-owed on Corey's box.** SHIP here can only ever mean "ship the design +
the bundled edit-round," never "the rendered behavior is proven." This is a proposal-with-bundled-WIP,
and I label it so: the human disposes on a longer time-horizon than the unit-green suites imply.

---

## 1. Decided direction

**Ship the Shot Card. Direction confirmed — correct, additive, on-strategy — gated on one round of
small, named edits and three measurements.** No redesign; every item is a token / keyframe / string /
conditional-class diff or a number. The room (10 design agents, none below 8, none calling for
redirection) and all five raters converge on "ship the direction, gated on one edit round."

**The delta-to-9 bundle (the iterate-round), prioritized:**

**P0 — honesty-surface, blocks the target of 9.** *(A false claim on the honesty surface is the one
thing this surface cannot ship with — delight built on a dishonest readback is trust-debt.)*

1. **Kill the false LTX footer claim** — gate the footer on `regionNotes.length > 0` (`Chain.tsx:801`).
   It currently claims "your tagged frames steer the picture" even when only frame-wide notes exist —
   verified: `noteEffect` honestly returns `''` for a frame-wide note (`Chain.tsx:526`), so the footer
   over-claims a steer that isn't happening. — *content-voice-designer (RISK 2) + interaction-designer.*
2. **Make the deterministic-floor path honest** — the suffix path (reached on BOTH an unreachable
   narrator AND a red-lined model fusion, verified `lucid_linear.py:1024-1031`) currently `sc-settle`s in
   identically (`theme.css:470`, unconditional), labels itself "reading…", and discloses "offline"
   (false on the red-line branch). Fix: **skip `sc-settle` when `source==='suffix'`**; **`'write'`→
   `'compose'`** in placeholder/label (true for both paths); **replace the false "offline" disclosure
   with the cause-agnostic "A plain reading — Lucid kept to your exact notes."**
   — *motion-designer (the gesture) + content-voice-designer (RISK 1/3) + brand-identity-designer (P1/P2).*
3. **Land the ADR-0025 amendment** with the corrected cost claim, the silence invariant, and the
   "Owed" list (§6). Behavior change is currently outrunning its decision record — the every-behavior-
   change-is-an-ADR non-negotiable is in play. — *design-technologist drafts; human disposes.*
4. **Take the two AA numbers** (§5, T6) — this is the single **un-fired cap**. — *consult
   `ui-accessibility-reviewer`; fix owned by visual-systems-designer.*

**P1 — trust + cost, closes feasibility/experience to 9.**

5. **Disable "Dream it" while a reviewed edit is stale-against-current-notes**, with a calm "updated
   below" rail; keep the server notes-digest gate (`lucid_web.py`) as the multi-tab backstop. The
   client already re-fuses eagerly on a notes change (`Chain.tsx:536-548`) and holds every signal
   (`fuseSeq`/`notesSig`/`fusedEdit`/`notes_digest`), so the common path into the round-trip-into-refusal
   can be removed before the click. — *interaction-designer (P2).*
6. **Bring the eager-fuse cost number + adopt TURN-phase backpressure** — instrument the `_fuse_cached`
   hit/miss seam (`lucid_web.py`): cold-fuse p50/p95, fuses-per-dwell under real typing, cache hit-rate,
   and the **`keep_alive:0` load/evict cycle count per compose** (`lucid_engine.py:680` — the fuse
   *loads and evicts* the 3B narrator per fire, it does not hold it warm). Then short-circuit
   `_fuse_cached` to the deterministic suffix when `TURN["phase"]=="dreaming"` so an eager fuse never
   thrashes the narrator slot against an in-flight beat. CPU/Ollama-measurable **today, no GPU box.**
   — *design-technologist; consult `resource-safety-reviewer` for the budget + eager-vs-lazy rule.*

**P2 — reversibility + polish, 9 → 10.**

7. **Demote the compose-card copper to a hairline edge** inside the Shot-Card variant
   (`theme.css:467-468`) so warm reads as a quiet boundary, not a fill competing with the blue readback
   rail; add the token-comment ratifying "copper = authored act that needs you, never decoration."
   — *visual-systems-designer + brand-identity-designer.*
8. **Name the `--dir-*-hue/-ink/-gly` direction triads** (`theme.css:376-379` are bare hex off three
   selectors — the exact `--brand-warm` re-hue-desync the file *already learned about*) and lift the
   three `noteEffect` phrases into one shared source. Pure ADR-0005 reversibility win — a re-hue tx
   then targets one named triad, not a scatter; **zero visual change.** — *visual-systems-designer.*

Two further content-honesty fixes ride the P0/2 string pass: drop the effect clause from the
remove-button aria-label (*content-voice RISK 5*).

---

## 2. Rating verdict + 10/10 gap plan

**Overall 7.6/10 — ITERATE (target 9). No hard cap fires.**

| Dimension | Weight | Score | The one thing it needs |
|---|---|---|---|
| Vision-fit | 0.30 | 8 | the ADR-0025 amendment to land **with the corrected cost claim** |
| Experience | 0.25 | 8.5 | the **two AA numbers** (the only path to an un-capped 9+) |
| Craft | 0.20 | 8 | the **false LTX footer claim killed** (blocker-weight) |
| Feasibility | 0.15 | 9 | the **eager-fuse cost number** at the `_fuse_cached` seam |
| Market-fit | 0.10 | 6 | promote the readback from spike-local UX to the substrate diff/revert wedge (**substrate-blocked**) |

Weighted arithmetic is **8.075**; the reported **7.6** is the aggregator's judgment hold — the four
design-heavy lanes do not get to average away market-fit's structurally-correct **6** (this is
spike-local UX touching none of the substrate moat). That gap is *by design*, not a defect in the work.

**The two live risks the 7.6 carries:**
- **The un-fired cap (P0.4).** If `.sc-txt` muted-row text over the warm-card-over-bright-frame-spill
  misses AA-normal 4.5:1, experience caps at **5** and the overall cannot exceed it. The number has
  not been taken. *This is the single thing that could turn a 7.6 into a RECONSIDER.* Until measured,
  the 7.6 is provisional.
- **The live P0 defect.** The false LTX footer claim is currently on the value-prop surface — an
  honesty surface that isn't honest. One-line diff; blocker-weight.

**The two disagreements the panel adjudicated (not averaged):**
- **Split A (market-fit 6 vs design 8–8.5):** different *objects* scored — "best honest-mapping UX
  move this surface has had" (true) vs "a move the market switches for" (correctly, no). Both kept;
  hence 7.6, not 8.1. The strategic question this names is escalated (§7).
- **Split B (feasibility 9 vs design 8/8/8.5 on the T4 cost):** the architecture is genuinely nil-
  risk (rides the existing sync handler + existing narrator, no new tier/runtime/daemon) and
  `keep_alive:0` bounds the cost to a transient load/evict, **not** standing residency — so the
  synthesis's "keeps the 3B narrator warm during the dwell" *over-states* it. The real, unmeasured
  defect is **load/evict thrash under eager debounced re-firing.** Fixed by measure + doc-fix + TURN-
  phase backpressure (P1.6); feasibility's 9 stands because closing it is a measurement, not a redesign.

**Projected re-rate (round 2 of 2):** craft 8→9, experience 8.5→9 (*if AA clears*), vision-fit 8→9
(amendment + cost claim), feasibility held 9, market-fit held 6 (item 9 substrate-blocked) → projected
weighted **~8.5** and a **SHIP-the-design** verdict, provided the AA cap stays clear.

**Substrate-blocked, cannot close this round (named with blocker):**
- *Market-fit 6 → higher:* promote the readback to the substrate-level diff/revert wedge (the same
  "here's the exact change agentosd will apply — edit or revert it" surface for an ADR-0005
  theme/wallpaper tx). **Blocked on the unbuilt apply/rollback tx (ADR-0005 is Accepted-but-no-code).**
- *The headline edited-reading-renders-to-screen e2e.* **Blocked on Corey's GPU box (ComfyUI).** This
  is the proposal-vs-code gap; no design owner closes it this round.

---

## 3. Market positioning (where this sits in the story)

**The substrate position (unchanged, the spine all messaging hangs on):** *AgentOS is the resource +
safety floor under your local AI — it keeps your live desktop and your LLM alive together on one GPU,
and lets you undo anything the agent changed.* Three pillars: **The VRAM truce** `[PROVEN]` · **Complete
control** `[DESIGNED, live PoC]` · **Yours, local, reversible, never dark** `[PARTIAL]`. Beachhead: the
local-AI tinkerer on a single 4090-class GPU running KDE Plasma 6. Category: **create "local-AI resource
substrate," refuse "AI agent platform."**

**Where the Shot Card lands:** it is **not** the positioning wedge — it is a *Lucid-lane surface*. But
it is the **best live demo of the reversibility wedge (Pillar 2 / ADR-0005) that currently exists** —
one rung above the `ui-audit` PoC, present-tense honest, on a creative surface. The readback **is** the
diff, the textarea **is** the edit, the ↺ reset **is** the revert — *all before commit, not after.*
Score its market-fit as **consistent-with-and-supportive-of** the position (the "honest, reversible,
you-see-what-runs" doctrine made tangible), **not** as the differentiator itself. That is exactly why
market-fit's 6 is correct and is not a defect.

**The 10-second clip the market team owns:** *type three words → the readback forms and settles in
Lucid's voice → you change "lantern" to "streetlamp" → you hit ↺ and Lucid's words bloom back.* That
clip is the moat — tag it a **Lucid-lane proof of the doctrine**, never the substrate wedge itself.

---

## 4. Signature delight moves to land

Delight runs **on top of** the honest surface — *after* the P0 fixes, never decorating a path that
didn't earn it (the in-app echo of the wallpaper's idle byte-identity discipline).

- **S1 — "Lucid reads it back, and waits"** (the one ownable beat). At a choice moment the readback
  *forms* (a calm light-sweep, `fr-sweep`/`formpulse`), then the exact prompt **settles in** rather
  than typing or popping (`sc-settle`), in the dream's serif voice on a model-voice-blue rail. *The
  agent shows you its hand before it spends your GPU, and lets you take the pen.* **The contrast is
  the delight:** the settle plays **only** when the model genuinely composed (suffix path gets a plain,
  instant appearance — P0.2) — so the gesture *means something* because the floor path doesn't get it.
- **The reset is the quiet hero (the "one more thing").** Peak-end theory: the *ending* is what's
  remembered, and the ending here is the moment a user — having edited Lucid's words — keeps theirs or
  takes Lucid's back. Give the ↺ reset a calm re-bloom of Lucid's text (one `sc-settle` reuse, reduced-
  motion-safe, zero GPU) + one line of warmth ("back to Lucid's reading") so reverting feels *safe and
  a little satisfying*, not a cold value-snap. This is undo-is-the-delight — AgentOS's deepest
  differentiator — in its smallest, most-repeated form.
- **S2 — the pin↔row "shared breath."** Hovering a numbered frame-pin ① lights its whole composite row
  and vice-versa (`hotNote`/`.sc-row.lit`). The most ownable *micro*-signature — it makes "your words
  bind to where you tapped" something the cursor *discovers*, and it is also load-bearing accessibility
  (number + shape glyph + hue, redundant-encoded). Keep as built; the lit glow stays the tag hue
  (`--aim`), never the scarce needs-you warmth (confirmed).
- **D1 — drop the `fr-twinkle` `rotate(10deg)`, keep the scale.** It is the lone rotating gesture in a
  grammar that is otherwise translate+opacity+scale; calm-by-default is the house register. Survives
  the "would they miss it if it were gone?" test as a *no*.

**Explicitly cut:** any "celebrate the commit" burst on Dream-it — confetti on a calm desktop, and
worse, it would celebrate a minutes-long wait that hasn't paid off yet. The honest completion signal
belongs to the desktop `agent.json` `warm` edge, not this surface. **No sound** is added anywhere (the
silence invariant is honored and recorded in the ADR — `sound-designer`'s 9).

---

## 5. Tensions and how they resolved (owner lane · tie-break)

- **T1 — dual-warmth adjacency** (copper compose-card wrapping the blue readback rail, `theme.css:467-468`).
  *art-director* (blocker FIX 1) reads it as muddying "which voice is warm"; *brand-identity-designer*
  (P3) reads copper-here as *earned*. Owner: **visual-systems-designer** (token) + **brand-identity-
  designer** (warm-meaning). Not a true conflict — both protect warmth-scarcity. **Tie-break: calm &
  honest ambient mapping.** Do **both** — demote the head-wash to a hairline (art-director option a)
  AND add the token-comment ratifying "copper = authored act that needs you" (brand P3). → P2.7.
- **T2 — the deterministic-floor path wears the "Lucid composed this" gesture.** *motion-designer*,
  *brand-identity-designer* (P2), *content-voice-designer* (RISK 1/3) land the **same underlying claim
  from three lanes** — stated once: *honest mapping must reflect true authorship* (the same law as idle
  byte-identity). Owner: motion (gesture) · content-voice (words) · brand (register). **Tie-break
  automatic** (calm & honest). → P0.2.
- **T3 — staleness gate is a post-click round-trip into a refusal** (`lucid_web.py` notes-digest gate).
  Owner: **interaction-designer** (P2, his named highest-residual-risk). Real defect on a surface whose
  reason to exist is honesty; the client holds every signal to pre-empt the common path. → P1.5.
- **T4 — the "no GPU on the review path" over-claim.** *design-technologist* + *design-researcher*:
  `fuse_direction`→`decompose_notes` runs a **local VLM pass** (`lucid_engine.py:680`, `keep_alive:0` →
  transient load/evict, not standing residency). "No new lease" ≠ "no GPU work." Owner: **design-
  technologist** + **resource-safety-reviewer**. **Tie-break: performant/yield-aware** — on a single-
  24GB substrate whose premise is GPU pressure, an under-stated cost claim is a drift; measure before
  taste (this repo's standing "nobody measured the reactive layer's marginal cost" pitfall). → P0.3
  (doc-fix) + P1.6 (number + backpressure).
- **T5 — token-system drift** (direction bundles + model-voice-blue as scattered literals,
  `theme.css:376-379`). Owner: **visual-systems-designer** (PROPOSE 1/2) + **brand-identity-designer**
  (co-owns `--voice-ink`). No conflict — pure refactor, *improves* reversibility (ADR-0005). → P2.8;
  `--voice-ink` unification is delta-to-9.5, defers.
- **T6 — two contrast spot-checks with no number.** *art-director, visual-systems-designer, design-
  technologist, content-voice-designer* all flag the same two unmeasured AA worst-cases: (i) the
  editable readback ink `color-mix(--inst-blue 24%, --inst-text)` — body-sized italic serif **content**,
  must clear AA-normal 4.5:1 (`theme.css:470`); (ii) `.sc-txt` muted rows over warm-card-over-bright-
  frame-spill (the text-on-uncontrolled-imagery hazard). **Owner: `ui-accessibility-reviewer` — I own
  no lane here; the correct output is *consult the missing voice*, not a ruling.** If (ii) fails, the
  smallest fix is named (the `.lib-cap` opaque-band floor, `theme.css:818`). A measurement gate, not a
  design choice. → P0.4.

---

## 6. Accepted tradeoffs

- **"compose" is slightly cooler than "write"** — accepted: honesty over warmth on the one surface
  whose value is trust (warmth lives in the serif voice + settle motion, not the verb).
- **Disabling "Dream it" while stale adds client-state coupling** — accepted: the round-trip-into-
  refusal is the worse cost.
- **The 3×3 `_region_phrase` grid stays coarse; object captioning stays deferred** — accepted and
  *correct*: *design-researcher*'s evidence is that a wrong object name ("lantern" for a streetlamp)
  breaks honest-mapping faster than honest vagueness does; the numbered pin already carries the precise
  referent. **Do not rush the caption pass** — validate "too coarse in practice" with real use first.
- **No prompt-history ledger** — explicitly rejected (*interaction-designer*): the readback lives at the
  site of the change; the dream tree is already the legible-history surface. A central prompt-diff log
  would be the ledger-as-hero anti-pattern in generative clothing.
- **P1 deterministic-path provenance** (*interaction-designer*) deferred — earns the last fraction
  toward 10 but is a backend span-contract; carried in the ADR "Owed" list, not dropped.

---

## 7. Recorded dissent (never erased)

- **`brand-identity-designer` vs `art-director` on T1 (partial, resolved-with-note):** brand reads the
  copper as a legitimate fourth warm meaning (the user IS needed here) where art-director reads the
  adjacency as muddying. Resolved by doing **both** (hairline demote + ratify-with-comment) — but
  brand's position is **recorded, not erased**: if the hairline demote ever reads as too cold, brand's
  ratification is the documented fallback.
- **`generative-artist` Proposal 2 (drop the `fr-twinkle` rotate):** an open taste call between two
  design-team members; **`art-director` holds the arbitration.** The delight pass and I both back the
  drop on calm-house-register grounds, but it is non-blocking and routed to art-director, not ruled here.
- **`motion-designer` Proposal 2 (carry the blue rail forward into the forming-hero ~400ms):** flagged
  as polish; defers to `art-director` on whether the forming hero should read as a clean slate.
  Recorded, non-blocking, **not** in the delta-to-9 bundle.
- **The strategic split (escalated, see §9.1):** *market-positioning* and *rating-aggregator* both name
  "is deep design investment in a throwaway spike's prompt UX serving the substrate doctrine, or scope-
  drifting into a creative-app product?" — an unresolved cross-lane question no rater owns. The delight
  pass's constructive read (this depth *earns* a demoable proof of the reversibility moat, serving the
  doctrine) is recorded as one side, not as the resolution.

---

## 8. Artifacts

This is a real behavior change on an **existing** ADR, so capture is an **amendment to the doc-of-
record, not a new ADR**. No new tier, runtime, or daemon — the fuse rides the existing sync web handler
and existing narrator (verified). Revert is a no-op for any dream without notes.

**`docs/adr/0025-lucid-dream-tree-and-spatial-feedforward-annotations.md`** — the 2026-06-21 amendment
needs, as the capture spec (I propose; code and the human dispose — I have **not** edited the ADR):
- **(a) corrected cost claim** (T4a): replace "NO lease/GPU" with — *"no ComfyUI lease / no heavy
  admission — the fuse runs one local VLM pass that **loads and evicts** the 3B narrator (`keep_alive:0`,
  `lucid_engine.py:680`), the same residency profile as a menu roll; the unmeasured cost is load/evict
  thrash under eager debounced re-firing, bounded by TURN-phase backpressure (P1.6)."*
- **(b) the silence invariant** (one sentence): *the Shot Card adds no completion sound or haptic; the
  honest completion signal belongs to the desktop `agent.json` `warm` edge, not this surface — no future
  contributor should add a commit `ding`.*
- **(c) an "Owed" list:** the GPU e2e of an edited reading rendering to screen; the eager-fuse cost
  number (P1.6); the subject-uncaptured-at-first-beat consistency test (*design-researcher* propose-2);
  the engine-toggle-mid-compose honesty seam (*design-researcher* critique-3); deterministic-path
  provenance (*interaction-designer* P1); object-caption follow-up if the location phrase proves too
  coarse.

**This brief:** `docs/design/0025-lucid-shot-card-FINAL-council-brief.md`.

**No new ADR stub** — the behavior change is an extension within ADR-0025's existing scope (the
annotation free-text already feeds the model and is gated like any prompt, ADR-0025; the fuse adds no
new actuation, tier, or irreversible op). The amendment above is the correct capture.

---

## 9. Open questions for the human (framed: options · costs · recommendation)

**9.1 — The eager-fuse VRAM-residency policy (the one real substrate question).** The Shot Card fires a
VLM pass eagerly during the dwell, right before a dream asks for the GPU; `keep_alive:0` bounds each
fire to a transient load/evict, but the thrash under debounced re-firing is unmeasured.
- (a) ship eager-as-built + measure (P1.6), accept the load/evict per fire;
- (b) ship eager + the **TURN-phase backpressure** so a fuse never competes with an in-flight beat;
- (c) make the fuse **lazy** (fire only on readback hover/focus) to eliminate speculative passes.
- **Recommendation: (b)** — keeps the "ready by the time you look" feel *interaction-designer* and
  *design-researcher* both value, while honoring performant/yield-aware. Cheap, fail-open, reversible;
  gate the final call on the cost number from `resource-safety-reviewer`.

**9.2 — The strategic scope question (escalated to me; I frame it for you, I do not rule it).** Is deep
design investment in this throwaway-spike's prompt UX *serving* the substrate doctrine, or *drifting*
into a creative-app product? The honest read from both market and rating lanes is that the Shot Card is
**not** the substrate wedge — but it **is** the most demoable proof of the reversibility wedge (Pillar 2)
that exists today.
- (a) treat the Shot Card as a sanctioned *doctrine demo* — keep the depth, tag every external use as a
  Lucid-lane proof-of-the-doctrine, and spend no *more* design depth here until the substrate trio
  (VRAM tx / proxy / threat model) catches up;
- (b) keep investing design depth in Lucid as a product lane in its own right;
- (c) freeze Lucid design at "honest + shippable" and redirect the ensemble to the substrate.
- **Recommendation: (a)** — it banks the demoable moat-proof (real value, on-strategy by *example*)
  without conceding that the creative app is the product. Consult **`ai-product-reviewer`** to sanity-
  check the doctrine-demo framing before any external use of the 10-second clip; this is the one cross-
  lane conflict no single agent owns, and it is yours to dispose.

**9.3 — The `fr-twinkle` rotate (low-stakes taste, two design-team members split).** Keep the rotating
spark (playful) or drop the rotate / keep the 1.22 scale (calm, matching the rest of the grammar which
never rotates). **Recommendation: drop the rotate** — the surrounding vocabulary is translate+opacity+
scale only; rotation is the lone editorializing outlier and calm-by-default is the house register.
Revert is one CSS value. Routed to `art-director` for the call.

---

## 10. Routing summary (who closes what)

| Item | Owner(s) | Consult |
|---|---|---|
| P0.1 LTX footer gate | content-voice-designer, interaction-designer | — |
| P0.2 honest floor path (settle/verb/disclosure) | motion-designer, content-voice-designer, brand-identity-designer | — |
| P0.3 ADR-0025 amendment | design-technologist | — |
| P0.4 two AA numbers | visual-systems-designer (fix) | **ui-accessibility-reviewer** (the numbers) |
| P1.5 stale-edit disable | interaction-designer | ux-reviewer |
| P1.6 cost number + TURN-phase backpressure | design-technologist | **resource-safety-reviewer** |
| P2.7 copper→hairline + token comment | visual-systems-designer, brand-identity-designer | — |
| P2.8 name direction triads + lift phrases | visual-systems-designer | — |
| S1/reset delight | motion-designer, interaction-designer | ambient-embodiment-reviewer, ui-accessibility-reviewer |
| D3 "compose" personification | content-voice-designer | brand-identity-designer |
| §9.2 scope call | **human** | ai-product-reviewer |

*All design proposals are advisory until the mediator decides and code disposes. The substrate-blocked
items (market-fit promotion; edited-reading e2e) cannot close this round and are named with their
blockers (unbuilt ADR-0005 tx; Corey's GPU box). I own no design lane — where a number or a ruling
belongs to a reviewer (AA, resource-safety, product scope), I route to them by exact name rather than
fill the empty lane.*
