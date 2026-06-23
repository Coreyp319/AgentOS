# Design-council brief — Aurora desktop look (ADR-0042)

Status: **RECONSIDER → ITERATE.** Overall **5.0/10 (a11y-capped; uncapped 6.6).** Aurora
is a productionized, default-off, fully-reversible 8/10-in-direction theme held below the
bar by **one reopened a11y blocker** (T1) plus two gated must-fixes (T2, T3). Do **not**
promote from default-off until P0#1 lands.

- Date: 2026-06-22
- Facilitator: design-discourse-mediator (neutral; reconciles, does not author design)
- Scope: the static **widget** identity — Aurora Union CSS style + Aurora Light/Dark scheme
  pair (`integrations/aurora-theme/`). NOT the reactive `nimbus-aurora` wallpaper.
- Relates to: ADR-0042 (decision of record — this brief reopens a closed must-fix and adds
  residuals), ADR-0005 (reversible-by-default), ADR-0034 (Style Charter spine).
- Inputs reconciled: art-director (8), motion-designer (7), visual-systems-designer (8),
  interaction-designer (7), design-technologist (9), generative-artist (8), brand-identity-designer
  (8), content-voice-designer (7), design-researcher (8.5), sound-designer (lane-empty);
  rating-aggregator (5.0 capped); market-positioning-synthesizer (8/10 position); delight pass.
- Source-verified this pass (the verdict pivots on these — all confirmed, not asserted):
  - `text.css:43` `outline: 2px solid var(--focus-color)` — keyboard focus ring at **0.45 alpha**.
  - `tables.css:36` `outline: 1px solid var(--focus-color)` — same defect on tree/table rows.
  - `variables.css:90` `--focus-color = set-alpha 0.45` — the 0.45-alpha fill token.
  - `variables.css:84-87` comment claims "the alpha lives only on the soft hover FILL … never
    the ring" — **false in source.**
  - `variables.css:75` `--changed-foreground: var(--neutral-text)` — benign edit on the amber ramp.

---

## 1. Decided design direction

**The spine ships; the bindings do not yet clear the bar.** Token-first derivation (everything
via `custom-color("kcolorscheme",…)`), the single-layer Union-honest elevation ramp, the
two-radius WhiteSur signature (6px controls / 12px floating surfaces), and `check-contrast.py`
wired as a hard refuse-to-install gate are all endorsed unanimously and verified against engine
source — not relitigated. Reversibility passes unanimously. The calm/restraint identity (one
violet accent, low chroma except accent, depth over flat fills, no state-coupling on widgets) holds.

**The two tasked reconciliations are resolved:**

1. **Elevation-ladder light/dark mirror → RESOLVED-KEEP.** Five agents independently
   (art-director, visual-systems-designer, generative-artist, brand-identity-designer,
   design-researcher) reached that dark-mode elevation conventionally *lightens-with-lift*
   while light leans on shadow (M3, Adwaita, Primer, HIG). A true numeric mirror would make
   Aurora *less* recognizably WhiteSur. We keep the asymmetry and **name it a locked invariant**
   so personalization respects it (dark `View` = recessed well; light `View` = brightest paper).

2. **Residual a11y priority → the focus-ring "residual" is actually a reopened BLOCKER.**
   interaction-designer flagged, and the mediator verified in source, that the original
   focus-ring must-fix was closed for buttons (`button.css:94` uses solid `--button-focus`)
   but **not** for text fields or table/tree rows. This is the exact <3:1 composite the must-fix
   claimed to eliminate, still live on the two surfaces where keyboard users spend their time.

**Division-of-labor rule (adopted, closes an over-trust edge):** widget Aurora = the resting
body, it *never signals*; the `nimbus-aurora` wallpaper = behavior, the *only* surface that
signals. A do-not-converge rule keeps the widget amber/neutral token off the wallpaper's scarce
warm `needs-you` token. The two share a name, not a meaning.

---

## 2. Rating verdict + 10/10 gap plan

**Verdict: RECONSIDER while the cap is live, collapsing to ITERATE the moment P0#1 lands.**
A hard a11y cap (keyboard focus at **2.71:1** View / **2.66:1** Window on text + table controls)
holds the overall at 5.0 regardless of the 6.6 uncapped average — and the tell that this is a
true cap and not a headline to average away is that the gap plan's first move *removes the
violation*. It is a two-line source edit, not an architecture rethink.

Weighted (Experience 0.30 · Craft 0.25 · Vision 0.20 · Feasibility 0.15 · Market 0.10 —
craft/experience over market per the brief):

| Dimension   | Score | The one thing it needs |
|-------------|-------|------------------------|
| Experience  | 5 (capped) | Rebind `text.css:43` / `tables.css:36` to solid `--focus-ring-color` → lifts cap to ~8 |
| Craft       | 8 | Pin T2 to one source-anchored contrast number, not the three-way wave-off |
| Vision-fit  | 6 (capped) | Redirect `.changed` off the amber hazard ramp to informational lavender |
| Feasibility | 8 | Land the gate rows (P0#2) *before* hand-tuning T2; back up `AnimationDurationFactor` |
| Market-fit  | 7 | Ship + generalize the contrast gate — the one move no competing Plasma theme has |

### Prioritized gap plan (owners are makers; the rater each closes is named)

**P0 — lifts the cap; blocks any promotion from default-off:**

1. **Rebind the focus outlines + correct the false comment.** `text.css:43` and `tables.css:36`:
   `var(--focus-color)` → solid `var(--focus-ring-color)` (match `button.css:94`). Fix the
   `variables.css:84-89` comment and the ADR line that record this as already done. Static binding —
   **no on-box render needed.** Owner: **ui-accessibility-reviewer** (disposition) +
   **design-technologist** (edit). Lifts Experience 5→~8, Vision, Market (ADR honesty).
2. **Extend the contrast gate to engaged states — specified as a test, not a direction.** Add
   `DecorationHover` / computed-hover-fill / focus-outline-composite rows to `check-contrast.py`'s
   NONTEXT block, **and assert every `:visual-focus` site resolves to `--focus-ring-color`** so the
   green-gate-over-bad-binding trap T1 exposed cannot recur. State thresholds + expected verdicts
   per row. Owner: **design-technologist**. Land this *before* #3. Lifts Feasibility, Craft, Market;
   it is the durable disposer for the whole T2 class.
3. **Fix dark interaction-state contrast — cover BOTH binding classes.** Bind hover to the
   already-shipped-but-unused `DecorationHover` (206,184,252 → **6.00:1** on Button bg — measured
   cheaper and far more legible than the originally-floated 0.13 mix at ~1.10:1), for *both* the
   border-only standard button (2.11:1) and the flat/toolbar fill (`button.css:317`, 1.29:1);
   separate checked/pressed by luminance in `check.css`. Owner: **visual-systems-designer**.
   Gated by #2.

**P1 — honesty + accessibility, cheap and reversible:**

4. **Redirect `.changed` off amber.** `--changed-foreground` / `--changed-background`
   (`variables.css:75-76`) → informational lavender + a non-colour channel (edge-bar/glyph) for
   CVD redundancy. A benign reversible edit must not read as a hazard on the calm-reversibility OS
   (the **calm & honest ambient mapping** non-negotiable). Owner: **visual-systems-designer**
   (token) + **interaction-designer** (state semantics). Reserve the revert *gesture* in ADR text
   only — the tx actuator is longer-horizon, correctly not built here.
5. **Reduced-motion lever with a backup.** Add `aurora-motion.sh` exposing `AnimationDurationFactor`
   AND back it up in `restore.sh` (today this global Qt knob has no inverse — a reversibility gap
   against ADR-0005). Owner: **design-technologist**.
6. **Console / revert copy honesty.** `apply.sh` "Revert everything" overclaims vs `restore.sh`
   leaving Aurora files installed; `aurora-mode.sh` claims pre-relogin liveness the CSS lacks.
   Owner: **content-voice-designer**.

**P2 — ADR coherence + standing gap:**

7. **Amend ADR-0042:** move T1 from "must-fixes applied" to **reopened blocker** (the
   `variables.css:84-89` claim is contradicted by `text.css:43` / `tables.css:36`); close the
   elevation-ladder residual as **resolved-keep** with the convention rationale; record the
   `.changed` redirect and the division-of-labor / do-not-converge note. Owner: **design-technologist**.
8. **Light-mode negative temperament** (`192,28,40` full-sat alarm vs dark's calm rose) →
   desaturated-but-AA rose. Lowest priority; defer to the token pass. Owner: **visual-systems-designer**.

**Three irreducible relogin-verification items** (cannot reach source-certainty in this harness —
GUI render segfaults): switch off-handle vs track, checked-vs-pressed in motion, HiDPI target size
on 6px controls. Record as an ADR relogin checklist; do **not** mark "verified."

**Expected after P0:** Experience ~8, Vision ~8, Craft ~9, Feasibility ~9, Market 7 → weighted
~8.1, no caps → **ITERATE toward 9**, with the P1 items as the remaining delta. SHIP reachable
in round 2.

---

## 3. Market positioning

**Position:** *A reversible, accessibility-gated desktop identity on KDE's next-gen CSS engine
(Union)* — for the un-Nixed KDE/CachyOS daily-driver running a WhiteSur-family Kvantum look who
cares about legibility and one-command revert.

**Category decision: JOIN "KDE Union/CSS theming," do not invent one.** A personal theme on a
published, named upstream engine has a real shelf to sit on, and joining it is *forward-aligned* —
KDE is sunsetting Kvantum/SVG in Plasma 7, so "a Union style" is the category KDE migrates *toward*.
Inventing "the self-improving desktop identity" would be a category lie the vision rater caps, because
the auto-proposer that would justify it is **deferred-not-shipped** (ADR-0042 Stage 2).

**Three pillars (each source-verified, maturity-tagged):**
1. **Legibility is a build gate, not a hope. [PROVEN/shipped]** — the WCAG-AA check is a hard
   `exit 4` refuse-to-install (`apply.sh:49-57`) beside a `union-ruleinspector` load gate
   (`exit 3`, `apply.sh:33-45`). Structurally defensible: the adaptive incumbent (Material You
   Colors) *cannot* add this without contradicting its mechanism — "any wallpaper → a palette"
   cannot also "refuse to ship a palette."
2. **Reversible without the Nix tax. [PROVEN/shipped, bounded]** — `css-tx.py` + `apply.sh`/
   `restore.sh` give one-command revert on a normal Arch/CachyOS box. **Honesty rail:** file/op-level,
   *not* Nix-store-atomic — never claim parity with plasma-manager generations.
3. **The WhiteSur look on the engine KDE is keeping. ["why now" tailwind, not a moat]** — the
   two-radius signature re-authored on Union while Kvantum/SVG (Aurora's own ancestor) heads for
   Plasma-7 sunset.

**Market-fit: 8/10.** One shipped, structurally-defensible edge the whole surveyed field lacks,
verified at *source* not ADR — unusually strong. Capped below 10 because the moat is **narrow**
(moat-by-combination, not moat-by-script — the gate is a weekend clone in isolation) and the
*exciting* "self-improving" story is roadmap, not shipped.

**Deltas to a 10/10 position (all *ship*, not *re-message* — the message is honest and sourced):**
(a) close the table-stakes a11y residuals so the claim graduates "contrast-gated" → "accessible";
(b) confirm Aurora is among the first authored non-Breeze Union styles (hardens P3 tailwind → fact);
(c) ship a slice of Stage 2 once Union's dialect stabilizes — the one move that converts a
strong-but-narrow 8 into category-*creating*.

**Messaging rails for content-voice-designer:** lead phrase *"guaranteed legible by construction"*;
frame the refuse-to-install as **valued safety, not friction**; market *"contrast-gated,"* never
unqualified *"accessible,"* until CVD redundancy lands; the Stage-2 proposer is **roadmap, never
present tense.**

---

## 4. Delight & differentiation — the signature moves

**Constraint that shapes the whole pass:** this is the *resting body*, not the reactive wallpaper.
Delight here may **not** live in motion or widget state-coupling — that would steal warmth from the
wallpaper's scarce `needs-you` channel and break the division-of-labor rule. So the delight lives in
the three things that are actually AgentOS, not WhiteSur: **the revert moment, the focus ring's
craft, and the honesty of the gate.** Nothing ships until P0#1–#3 lift the cap — a flourish on a
sub-floor focus ring is anti-delight.

**S1 — The revert that proves nothing was lost (the "one more thing").** Make `restore.sh` say
what it gave back: one line, reading the two values `apply.sh` already backed up —
*"Restored: widgetStyle → kvantum, ColorScheme → BreezeDark. Your desktop is exactly as it was."*
This turns AgentOS's hardest-to-feel differentiator (that change here is *safe*) into a felt,
repeatable, tell-a-friend moment. Costs one `echo`. The undo *is* the delight. Owner:
**content-voice-designer**.

**S2 — The lavender focus ring as the single signature thread (after P0#1).** Once the outlines are
rebound to solid `--focus-ring-color`, unify them to **one** token-driven weight/colour across button,
text field, and table row (today: 2px text, 1px table). Every Tab keystroke quietly reaffirms one
calm identity. The accessibility fix *becomes* the signature — the most AgentOS move possible: the
honest move and the beautiful move are the same move. Owner: **visual-systems-designer**, confirmed by
**ui-accessibility-reviewer**.

**Earned microdelights:** the refuse-to-install message reframed from error → care ("Aurora won't
install a colour scheme that drops below readable contrast — yours scored X. Fix the pair, or use the
shipped scheme"); a one-line WhiteSur-lineage note on apply success; disabled controls draw **no**
focus ring (a ring on a dead control is dishonest); the dark-mode 1px scheme-bound hairline present
on all three elevation rungs, not just popups.

**Lane stays empty by design:** Aurora ships **silent** — no earcon on apply/revert. A chime on a
desktop theme is the easiest calm-killer. sound-designer escalates the off-by-default earcon grammar
as a *separate* doc; it is not built here.

---

## 5. Accepted tradeoffs

- The WhiteSur dark-well asymmetry means `--view-background-color` carries opposite elevation
  semantics per mode — accepted, mitigated by **naming it a locked invariant**. We accept asymmetry
  over a less-recognizable mirror.
- The dark hover fill trades a hair of flat-restraint for legibility; held to a measured token
  (`DecorationHover`, 6.00:1) it stays under selection fill.
- `AnimationDurationFactor` reduced-motion is global (all Qt apps), not Aurora-scoped — accepted and
  correct (reduced-motion *should* be global).
- We ship the `.changed` redirect and division-of-labor as token/text edits now but **reserve**
  (do not build) the revert actuator and the shader-derived accent — the tx layer and accent-regen
  are longer-horizon.
- Joining the Union category (not inventing one) means competing on the Breeze-Union shelf — we win
  it on the gate + reversibility, not on novelty-of-category.

---

## 6. Recorded dissent (never erased)

- **art-director** holds that "one name, two unrelated accents" (violet widget accent vs the shader's
  warm signal) is the single biggest gap (~1.0 of his delta) and that safe-by-accident must become
  safe-by-design via a **shader-derived accent**. The room did not adopt this as bar-clearing — it is
  escalated (§7 Q1), not erased.
- **motion-designer** rates the light↔dark hard-cut a ~3-point blocker *in his lane*. The room treats
  the crossfade as a craft enhancement (no non-negotiable compels it; the reduced-motion *lever*,
  which is a non-negotiable, is adopted). Dissent recorded (§7 Q2).
- **sound-designer** is lane-empty but insists the rebrand must not silently extend "ambient identity"
  to an undesigned audio channel. Adopted as the one-line name-scoping note; the full earcon grammar
  is escalated as a separate doc, not built here.

---

## 7. Open questions for the human (options + cost + recommendation)

1. **Shader-derived accent (art-director's vision push).**
   - **(A)** Keep the current violet accent; ship the division-of-labor sentence only — cheapest,
     fully reversible, closes the over-trust edge but not the "co-lit" vision gap.
   - **(B)** Regenerate the Aurora accent from the idle Hills/Flow shader hue so the chrome reads as
     lit *by* the environment — higher craft ceiling, but ripples through both schemes + the contrast
     gate and needs generative-artist to pin the sample hue + a clean `css-tx.py` checkpoint.
   - **Recommendation: A now, B as a deliberate follow-up.** B is real value but it is
     architecture+taste, not a non-negotiable, and the must-fixes (P0) must not wait on it.

2. **Light↔dark crossfade (motion-designer's blocker-in-lane).**
   - **(A)** Accept the hard cut (deliberate user-initiated toggle, no WCAG flash breach); add only
     the reduced-motion lever.
   - **(B)** Spike a ~300ms KWin fade through the mode-invariant accent.
   - **Recommendation: A now** (the reduced-motion lever is the actual non-negotiable and is adopted);
     **B as an optional relogin-verified craft spike** — it cannot be rendered in this harness.

3. **Dark scheme `contrast=4` bump (ADR S2 residual).**
   - **Recommendation: leave it.** The unified shadow ramp carries separation; a bump is the
     least-calm lever. Revisit only if the relogin pass shows a popup-over-its-own-window fails to
     separate.

---

## 8. Artifacts to capture (this brief proposes; code + human dispose)

- **This brief:** `docs/design/0042-aurora-desktop-look-council-brief.md` (written).
- **Amend** `docs/adr/0042-continuous-ui-improvement-via-union-css.md`: (a) T1 →
  **reopened blocker** (the `variables.css:84-89` focus-ring-alpha claim is contradicted by
  `text.css:43` / `tables.css:36`); (b) elevation-ladder residual → **resolved-keep** with the
  convention rationale; (c) add the division-of-labor + do-not-converge note; (d) reserve the
  `.changed` revert-anchor in ADR text. **This is a behavior-relevant correction to a decision
  record → it is itself an ADR move.**
- **New** `docs/design/` stub for the Aurora earcon grammar (off-by-default, two-earcon vocabulary) —
  routed to sound-designer + content-voice-designer, NOT built in this CSS work.
- **Source edits (disposing session):** `variables.css` (`.changed` tokens, hover-fill token, the
  false comment), `button.css`/`check.css` (engaged-state ramp), `text.css`/`tables.css`
  (focus-outline rebind), `check-contrast.py` (engaged-state rows + focus-site token assertion),
  `apply.sh`/`restore.sh`/`aurora-mode.sh` (copy + the S1 restoration receipt + `AnimationDurationFactor`
  backup), new `aurora-motion.sh`.

---

## 9. ADR stub implied (behavior change present)

A behavior change is implied — the focus-ring rebind changes a rendered a11y affordance, the
`.changed` redirect changes how an honest signal is mapped, and the gate extension changes what
refuses to install. **No new ADR number is needed:** all of it is an **amendment to ADR-0042**
(scoped above in §8). The single load-bearing correction is that ADR-0042 currently records a
WCAG pass that source contradicts — that false claim is itself a defect to fix, not just a residual
to add.

**Bottom line:** Aurora is a strong, honest, reversible 8-in-direction held at 5 by a two-line
binding defect and a false comment. Land P0#1 (rebind + comment + ADR correction), then P0#2 (gate
the class so it cannot recur), then P0#3 (dark hover via the unused `DecorationHover`). The cap lifts,
the verdict collapses to ITERATE, and SHIP becomes reachable in round 2. Do not promote from
default-off until P0#1 lands.
