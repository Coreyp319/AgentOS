# ADR-0052: The sidekick-creature embodiment grammar — a bounded second register, confined to the Check-ins tab

- Status: **Accepted** (2026-07-01) — the §Consequences reviewer gate ran twice: the 2026-06-29
  two-lens pass below (must-fixes applied), and the ADR-0050 shell-wide gate confirmed those fixes
  **survived the primary-face refactor** (copper split, steady `!`, open-eyed reduced-motion
  stills, decorative `Accessible.ignored`, flash math all verified intact). Built as Phase A; the
  visual grammar the Check-ins tab (ADR-0050) renders over the schema-5 rows (ADR-0051).
- Date: 2026-06-29
- Deciders: Corey (binding product steer: "we'll need many ADRs"; "at the very least" a buildable
  read-only floor), design synthesis from three Plan agents.
- **Amends ADR-0012 §7** — adds a **bounded second visual register** to the keyhole (the first
  register, the calm arbitration instrument, is unchanged). Relates `docs/vision.md` (ambient
  embodiment — "ignorable until you choose to read it"), ADR-0030 (bounded-scalar / warm-monopoly
  invariants), ADR-0042 (instrument design tokens), ADR-0003 (fail-open). Honors ADR-0012 §7's
  porthole-shader exception precedent (a bounded GPU departure justified by VRAM, not compute).

## Context

The keyhole is deliberately **calm / ambient / minimalist**: ADR-0012 §7 fixes one warm element max,
state never carried by color alone, near-zero-GPU; `docs/vision.md` calls it *"ignorable until you
choose to read it."* The Check-ins design renders each Hermes task as a **playful avatar with mood
faces** — a genuine **design-language departure**. Left unreconciled it risks four specific
violations: (a) **`needs_you` impersonation** (a sad creature reading as the reserved attention
signal), (b) a **warm-budget violation** (a second warm hue competing with the one reserved warm),
(c) **color-only state** (mood read from face color), and (d) **strobe** (many independent
per-creature animation timers). This ADR defines the grammar so the delight is real *and* the
non-negotiables hold by construction.

## Decision

1. **Define the creature grammar.** Each card carries a sidekick: an **ASCII face** (e.g. `(◔‿◔)`)
   plus an optional pixel sprite, with **mood faces driven by the raw `status` + `consecutive_failures`**
   from schema 5 (ADR-0051) — the *view* maps those to `calm / working / stalled / needsyou / done`,
   the producer never emits a mood word. Idle motion is a gentle **bob / sway / blink**; a user
   **poke** triggers a **squash** plus a ~7-tick **"blurt"** (a brief speech flourish).

2. **CONFINE the register to the Check-ins tab only.** The creatures live **exclusively inside
   tab 2.** The **Instrument tab, the tray glyph, and the wallpaper stay calm** — unchanged by this
   ADR. This is the whole reconciliation: §7's calm is not relaxed *system-wide*; a second, livelier
   register is admitted in **one bounded surface** the user explicitly navigated to, exactly as §7's
   shader exception was scoped to the one foveal porthole. *(Amended 2026-07-01, ADR-0050
   primary-face flip: the confinement is to the Check-ins **view**, whichever tab position it
   occupies — the tick gate binds the view's own `visible`, never a tab index — and the popup-open
   click is the explicit-navigation consent; the Instrument tab, tray, and wallpaper stay calm
   exactly as written.)*

3. **Inherit §7's invariants — they are not relaxed, only localized:**
   - **Never spend the reserved warm on anything but `needs_you`.** The warm-**predicate** monopoly
     (ADR-0012 §7 / ADR-0030) holds exactly: `warm` (#FF9957) is spent **only** on the `needs_you`
     predicate (kanban `review`); **no other mood touches it** (`working→blue`, `stalled→amber`,
     `done→sage`, `calm→dim`) and **no second warm hue is minted**. The literal "one warm *element*"
     count was a property of the calm tray/ambient layer, not a card list — N review tasks legitimately
     render N warm-bordered cards; the aggregate is kept calm by a low border alpha (≤0.4) and the
     redundant word/shape, not by faking a single element. Crucially the warm is the GLOW/halo/border/
     dot **only**: the needs_you face glyph + the status WORD use the AA-safe **`warmText` split**
     (copper `#A8480F` on the light register), exactly like the tray glyph — warmth never rides the
     letterforms (ADR-0012 §7 amendment), so the one line the user most needs to read stays legible.
   - **Mood is redundant — shape + text, never color-only.** Mood is carried by the **face
     characters** and the **status word**, so it **survives grayscale**. Color may accent; it never
     *is* the signal.
   - **Reduced-motion freezes to a single representative OPEN-EYED still** per creature: the effective
     motion gate (`animate && !reducedMotion`) drives the blink/spinner/sway/bob/sparkle **and** the
     poke, so a still is the resting face — never mid-blink, never a stuck spinner glyph, never a
     latched poke, never a strobe, never a frozen black.

4. **One shared ~9fps (110ms) tick drives all motion — no per-creature timers.** A single shell
   `Timer` advances every creature's bob/blink phase, **gated on**: tab-active **AND**
   popup-expanded **AND** `!reduced-motion` **AND** `!unknown` (an unreachable Hermes stops the tick
   and suppresses the cards — it never animates stale data; the body shows an honest "Can't reach
   Hermes"). An **animated-creature cap (~10)** bounds how many move at once (the cap bounds motion by
   index — over-cap and reduced-motion cards render as stills; a per-card phase offset keeps the
   animated ones from bobbing/blinking in lockstep). This is **bounded GPU like the
   ADR-0012 §7 porthole-shader exception** — and *cheaper*: creatures are plain QtQuick `Text` /
   `Rectangle`, no shader, no Canvas, so the guarded resource (VRAM for inference) is never touched
   and the marginal compute is below the porthole shader the instrument already ships.

5. **Poke and blurt mutate view-state only.** A poke changes nothing but local QML state — **no IPC,
   no D-Bus, no system write.** The interaction stays fully inside the **read-only charter**
   (ADR-0053); the creature delight is the one *live* interaction Phase A offers, precisely because
   it touches nothing outside the popup.

6. **New tokens live in the token system, no inline hex.** The per-register creature mood tokens are
   defined in `InstrumentPalette.qml` and canonicalized in `integrations/design/instrument-tokens.md`
   (the ADR-0042 token home) — never inline literals, so the register stays coherent and AA-auditable
   like the rest of the instrument.

## Consequences

- **A genuinely new visual vocabulary enters AgentOS.** Gate it through the same reviewers the
  keyhole's aurora and condensed-row work passed: **design-council**, **ambient-embodiment**, and
  **ui-accessibility** (both-register AA, reduced-motion, the no-`needs_you`-impersonation check —
  discharged here by the warm-token reuse + the shape/text redundancy, mirroring how the §7
  condensed-row ember discharged it by being breath-free).
- **The wallpaper is the whole-fleet mood; the creatures are the per-task register.** They **share a
  system, not a meaning** — the wallpaper says *"the fleet, overall, is …"* while a creature says
  *"this one task is …"*. Keeping them distinct in scope prevents the per-task liveliness from
  leaking back into the deliberately-calm ambient layer.
- The shared-tick gating (tab-active + expanded + reduced-motion + cap + off-screen-freeze) is the
  load-bearing performance contract; it is the analog of the porthole's frame-cap/visibility pause.

## Status / next

Proposed; Phase A. The grammar is read-only and view-local (poke/blurt mutate nothing). Reviewer
gate as above before `Accepted`. Companions: write actions on a card are **ADR-0053** (the seam) and
land with **ADR-0054** (the write client, Phase B); tok/s and cost on a card wait for **ADR-0055**
(Phase C). No producer change is required by this ADR — it consumes the schema-5 rows (ADR-0051).

## Review (2026-06-29) — ambient-embodiment + ui-accessibility gate run; must-fixes applied

Both reviewers returned **ITERATE**; the strengths confirmed the load-bearing invariants
(warm-predicate monopoly, grayscale-safe redundancy, bounded + scope-contained motion). The flagged
must-fixes are now **applied** (offscreen-render-verified in dark, light, and UNKNOWN):

- **Reduced-motion was a dead property** — the `animate` gate never folded in `!reducedMotion`, so a
  frozen tick could freeze a creature mid-blink / mid-spinner. Fixed: `_animate = animate &&
  !reducedMotion` drives every motion path + the poke (no latch).
- **UNKNOWN kept animating stale cards.** Fixed: the shared tick + the card Repeaters now gate on
  `!unknown`; an unreachable Hermes goes honestly quiet ("Can't reach Hermes"), no bobbing.
- **`needs_you` warm failed AA as text on the light register (1.83:1).** Fixed: a new
  `creatureNeedsYouText` token routes the needs_you face glyph + status word through the copper
  `warmText` split; the raw warm stays on halo/border/dot only.
- Plus: the `!` ask is now **steady** (not a ~1 Hz nag-blink); the latent 4.5 Hz pixel sparkle slowed
  + motion-gated (WCAG 2.3.1); per-card **phase offset** so a row doesn't pulse in lockstep;
  decorative faces/canvas/child-texts set `Accessible.ignored`; the tab switcher + filter chips made
  keyboard-operable with focus rings; disabled action buttons announce as StaticText with the "why".
