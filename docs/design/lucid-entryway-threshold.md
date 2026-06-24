# Design brief — Lucid entryway "The threshold, not the form"

**Status:** Decided direction · ITERATE→ship-the-design (round 1 baseline 8.0/10, target 9.3)
**Scope:** Frontend visual + interaction redesign of one React component — `Start.tsx` — and additive
styling in `theme.css`. **No backend / API / contract / safety-semantic change.** Reuses only shipped
kit (no new tokens, no new card grammar).
**Author:** design-discourse-mediator (facilitator) — reconciling art-director, motion-designer,
visual-systems-designer, interaction-designer, design-technologist, generative-artist, sound-designer,
brand-identity-designer, content-voice-designer, design-researcher; plus the rating panel, the market
positioning brief, and the delight pass.
**Disposition:** Code and the human dispose. This is a proposal.

---

## 1. The decision in one line

Recompose the dense settings-style "Start a dream" form into a **cinematic threshold**: one narrative
invitation, the *describe* path promoted to the single hero bound to a filled-blue Begin CTA, image-seed
demoted-but-always-visible, name incidental, Private/Mature as a calm declaration strip, behind a settled
sub-`develop`-opacity cool aurora ground — all built from grammar the product already ships. The
functional + safety contract is preserved byte-for-byte; two pre-existing `aria-live` defects are fixed
in the same diff.

**Why now:** this is the first thing a new user sees, and today it reads like a config form (the brief's
verdict). The disease is **affordance-equality**, not a weak CTA: the three seed inputs (`Start.tsx:54-64`)
render at identical `label.block` weight when the product thesis is "describe the opening." Fix the
hierarchy and the CTA symptom dissolves with it.

---

## 2. The four load-bearing constraints (tie-breakers, not taste)

These resolved real tensions in the room. Each is a non-negotiable applied in its stated priority order
*before* any aesthetic argument. Violating one is a drift, not a preference.

1. **Cool-only resting threshold; one warm source preserved.** The resting ground and the Begin CTA are
   **instrument blue, never copper**. `--brand-warm` (copper) is the *scarce* "needs-you" signal reserved
   for the B2 consent card and the Mature-on fact only (`theme.css:8,17-19,276`). A second warm source —
   a "magical" warm glow on the CTA — dilutes the one moment that must be warm and is **cut**. Tie-break:
   *calm & honest ambient mapping* (warmth must mean exactly one thing). Owner: brand-identity-designer,
   co-signed generative-artist.

2. **Safety captions never *fully* hidden; one short Mature line always visible.** The grey-caption wall
   (`Start.tsx:53,54,57,62,67,71,73`) is the readability defect — collapse the *optional* captions
   (B2-at-rest, the save note, Private's lifecycle tail) into context/disclosure. But the Mature line
   stays visible, and **the red-line sentence ("Minors and real people are always blocked") stays
   standalone and always-visible — never collapsed, never nested inside the checkbox `<label>`.**
   Tie-break: *local-first / consent*. Owner: content-voice-designer; **binding ratifier:
   responsible-ai-privacy-skeptic.**

3. **Idle/rest reads calmer than work.** The aurora ground must **settle and stop** (the existing
   `develop` ends `opacity:.6`, `animation … forwards`, `theme.css:324`), cap opacity *below* the
   `develop` endpoint, and read quieter than an active develop. Ship the **static low-opacity cool
   gradient as the default**; the animated settle-and-stop is the polish tier that must *prove* it reads
   calmer and clears AA. Tie-break: *calm & honest ambient mapping* (rest must never look busier than
   work). Owner: generative-artist + motion-designer.

4. **No new tokens, no new card type, no backend touch — reuse the shipped kit.** `.disc` disclosure
   (`theme.css:55-65`), `.aurora`/`develop` (`:136/:324`), `.tag-mature` (`:276`), `.consent`/`.beat warm`
   (`:113-117`), `.sc-reset-echo`/`sc-settle` undo-delight precedent (`:532-537`), the `.future-go`/
   compose-card filled-primary grammar, `--display` Fraunces (legal on narrative lines only, `:2-4,13`).
   Tie-break: *don't reinvent* (ADR-0001, applied inside the app). Owner: visual-systems-designer +
   design-researcher.

---

## 3. The direction — concrete

1. **One narrative invitation.** Fraunces hero line in the dream's voice — e.g. *"What do you want to
   dream?"* — with a small system-font eyebrow ("New dream"). Replaces the redundant `card-title` + note
   (`Start.tsx:52-53`).
2. **Describe = the hero.** A roomy field, serif-italic placeholder (the dream's voice), **system-font
   upright typed text** (the user's voice — the screen visibly hands over the pen), bound directly to the
   **Begin CTA promoted to a filled-blue primary** (clone `.future-go` grammar, *not* the left-aligned
   secondary `.beat` at `:74`). Cool, not copper.
3. **Image-seed = always-visible named secondary.** "or start from an image" reveals a **styled** file
   trigger (label wraps a hidden real input); the raw native control (`Start.tsx:60`) never shows at rest.
   `fileRef` and the `onChange` consent-clear (`:60`) preserved exactly. Demote-but-keep-visible — the
   upload re-opens the B2 likeness gate, so it must never be buried.
4. **Name = incidental.** Smallest, optional, below the hero (renameable post-hoc via `LibraryCard`, so
   demotion costs nothing).
5. **Private + Mature = a calm "before you begin" strip.** Real `<input type=checkbox role=switch>`, state
   never color-only (glyph + position + `aria-pressed`), one short caption each. Mature-on wears
   `.tag-mature` copper (a calm fact, never red); the red-line sentence stays standalone.
6. **A settled, sub-`develop`-opacity cool aurora ground** behind the card (reuse `.aurora`/`develop`/
   `.grain`, `aria-hidden`, honors `prefers-reduced-motion` / `prefers-reduced-transparency`). Cool only.
7. **Copy re-rank:** start `msg` **empty** (not `B2_NOTE`); delete the save note (`:73`); relocate B2 to
   file-chosen; tighten captions; keep a persistent live region across the consent ternary.

---

## 4. Signature delight (build from shipped grammar; spend nothing new)

**S1 — "The opening develops, it doesn't load." (the one ownable beat / the "one more thing").**
On Begin from a *described* opening, the hero field does not blank to a spinner — the **`develop` aurora
blooms in place behind the words the user typed** (`theme.css:136/324`), the same cool dawn the stage and
keyhole wear. The opening *forms* out of the dark, in the same box, before the route flips to the chain.
This makes the product's deepest promise — "a frame developing, not a spinner" (ADR-0014) — true at frame
zero, the first second a user meets Lucid. Continuous submit→render state is a clip no competitor can show.
Pure compositor, ~0 VRAM, survives a wallpaper kill (it's DOM, not shader). Owner: motion-designer +
generative-artist.

**S2 — "Cancel feels like nothing was lost." (peak-end, on the one warm channel).**
Promote the consent-card Cancel (`Start.tsx:85`, today a flat `setMsg('Cancelled.')`) to the shipped
**`.sc-reset-echo` / `sc-settle` grammar** (`theme.css:532-537`) — the calm italic "revert is safe"
settle the product already uses. "Cleared — nothing was uploaded" *settles* in. The scariest moment on
the screen (a real-person-likeness decision) ends in reassurance, and lands on the *one* warm surface
already allowed to be warm — zero cost to the scarce warm budget. Owner: interaction-designer +
content-voice-designer.

**Microdelights (calm, reward the 100th encounter):** Begin blooms once on press (`gbloom`/`bloom`-lite),
never an idle pulse, never on hover · the serif invitation answers in system-font when you type · Mature-on
is a calm copper fact, never an alarm.

**Cut (gimmick guard):** no second warm source (S1/Begin stay cool) · no earcon / CTA click sound (bake a
one-line "no audible cue here" contract at the consent site so the overhaul can't grow one) · no confetti /
success burst.

---

## 5. Differentiation made felt (one market edge → one moment)

- **VRAM-truce (north-star edge):** felt as the *absence* of the OOM the user feared — Begin
  **admits-or-queues instead of crashing** (ADR-0041 arbiter, on-box PASS=11/0). Keep the existing calm
  `ReadyChip` (`App.tsx:101`); **do not add a second readiness badge** competing with the hero, and never
  imply "free under load" (unmeasured).
- **Reversible-by-default:** S2 (Cancel = nothing lost) is the best felt expression of the moat, and it
  already lives on this screen.
- **Local-first / privacy:** B2 relocated to file-chosen, framed as a gift ("we checked for likeness and
  stripped location/camera metadata") not a watch. Wording to responsible-ai-privacy-skeptic.
- **Ambient embodiment:** S1 uses the *same cool dawn* as wallpaper + keyhole — one color language,
  wordmark to wallpaper.

---

## 6. Preserved byte-for-byte (the line the redesign must not cross)

In-page consent card (not native confirm, `Start.tsx:75-90`) · seed-swap-clears-consent (`:60,63`) ·
`requires_consent`→card vs hard-block→plain refusal (`:38-43`) · Mature floor + red-line semantics
(`:11-15`) · the `data-view-heading tabIndex={-1}` focus heading (`:52`) · `fileRef` upload path · the
three seed paths (image / describe / abstract fallback) · Private-session in-memory wipe (`:66-67`).

---

## 7. Two pre-existing `aria-live` defects — fixed in this diff, **gating**

Not redesign choices — known bugs with known one-line fixes. A redesign that ships *without* these commits
the accessibility failure the current code already has (cap-eligible).

1. **`msg` initializes to `B2_NOTE` (`Start.tsx:18`)** → the `aria-live` region announces a privacy
   promise about an un-taken action *on mount*. **Fix: start `msg` empty** (calm-default — absence of a
   flag is the message).
2. **The consent branch is a non-live `role="group"` (`:76`)** while the live region only exists in the
   `else` (`:89`) → the one true "needs-you" moment can go unspoken. **Fix: mount one persistent
   `role="status" aria-live="polite"` region across both ternary branches**, and write the consent reason
   into it.

Also fix the reduced-motion floor: `.aurora{opacity:.5}` (`theme.css:328`) is *brighter* than the sub-`.6`
resting intent — drop it at or below the resting cap.

---

## 8. Prioritized 10/10 gap plan (round-1 → 9.3)

Owners are makers, never raters. Each closes a named rating delta.

1. **Ship both `aria-live` fixes in the round-1 diff — GATING (cap-eligible if skipped).** `msg=''`; one
   persistent live region across both consent branches. — *interaction-designer.* Closes Experience.
2. **Pin the hero type/field/placeholder spec with measured AA contrast.** Exact hero size/weight (clone
   `.beat-q` 1.4rem or a named step up), describe-field `min-height`/padding, placeholder token
   (`--inst-label` clears AA 4.53:1 on glass; `--inst-muted` likely does *not* on `--inst-deep`) + the
   ratio it clears. Keep a programmatic `<label>` so the serif placeholder is never the sole label (WCAG
   3.3.2). — *design-technologist + visual-systems-designer.* Closes Craft.
3. **Render-prove aurora-behind-glass; default to the static cool gradient.** Run `check-contrast.py`
   against the brightest settled-aurora frame for `--inst-label`, the placeholder tone, and `.disc-k`
   over the default transparent path; fix the `:328` reduced-motion opacity. Animated ground ships only
   if it clears AA. — *generative-artist + motion-designer, prototype by design-technologist.* Closes
   Feasibility + Vision-fit; resolves the §6 craft-overstatement by *showing* the math.
4. **Style the file-input; demote-but-keep-visible image seed; standalone red line; relocate B2.** —
   *interaction-designer + content-voice-designer.* Closes Craft + Experience.
5. **State the decision, not the menu; name the revert.** Recommend the staged subset as the round-1
   landing (see §9); add the revert note: `git revert` of the single `Start.tsx`+`theme.css` diff, no
   services/config touched, ADR-0005 N/A. — *design-technologist + brief author.*

---

## 9. Open questions for the human (options + recommendation)

1. **Image-seed weight (the one live tension).** (a) collapsed `.disc` disclosure, summary always named;
   (b) always-visible segmented tab (Describe / Image / Surprise me). *Cost:* (a) calmer, adds a click to
   a consent-gated path; (b) louder, more discoverable. **Recommend (a) now** (reuses shipped `.disc`),
   route real upload-vs-describe frequency to ux-reviewer, flip to (b) only if upload proves common.
   Either keeps the B2 gate one obvious click away.
2. **Scope this round.** (a) full §3 including the aurora ground; (b) the 80%-risk-free subset (reorder +
   styled file input + filled CTA + caption collapse + the §7 a11y fixes), aurora as a phase-2 follow-up
   gated on gap #3. **Recommend (b) first** for a fast near-zero-risk landing, then (a) as the polish
   pass — the hierarchy fix carries most of the value and none of the contrast risk.
3. **Before any build — three consults gate the merge.** **responsible-ai-privacy-skeptic** (red-line /
   Mature framing — binding) and **ui-accessibility-reviewer** (aurora-behind-glass contrast re-run +
   ratify the §7 live-region fix). **ux-reviewer** owns Q1 frequency. **Recommend gating the merge on the
   first two** (safety + a11y are non-negotiables); let ux-reviewer refine in a follow-up.

---

## 10. Market position this front door must carry (and not over-claim)

Lead noun: **substrate / floor** under your local AI (not "agentic desktop"; "desktop" is the *surface
where you feel it*, never the category — ADR-0001; Microsoft owns "Agent OS" as of Build 2026). The
entryway is exactly where the wedge is felt: the local-creative-AI daily driver on one 24GB card hits
Begin and it admits-or-queues instead of OOMing. **Honesty guards on this screen:** never imply the
wallpaper "magically sheds VRAM" (live shed out of scope, ADR-0004) and never imply "free under load."
The reversibility pillar is felt here via S2 and the consent gate; the privacy pillar via the relocated
B2 line — claim "provable on-box for the data path," not "safe daemon."

**Recorded dissent (not erased):** interaction-designer / visual-systems-designer / design-researcher
prefer a collapsed `.disc` for image-seed; art-director / generative-artist / brand-identity-designer /
design-technologist insist it stay always-visible. Resolved to demote-but-keep-visible; the
disclosure-vs-tab subform is the Q1 escalation. design-technologist's cheaper-path reservation (the §9
Q2 staged subset) is recorded as the staged fallback, not a disagreement. No agent dissents from the core
direction.

---

## 11. Why no ADR

No contract, safety semantic, or behavior changes — this is a visual + interaction re-layout of a
`spikes/` web component. An ADR stub is **not** warranted. The one path that *would* require an ADR is
explicitly out of scope: if the consent-ground ever *reacts* to readiness/fleet state (a closed loop, not
decoration), that is a behavior change and earns its own ADR. Flagged so it cannot slip in silently.

**Files of record:** `spikes/dreaming/lucid/web/src/Start.tsx` · `spikes/dreaming/lucid/web/src/theme.css`
(reuse `.disc` 55-65, `.future-go`/compose-card, `.aurora`/`develop` 136/324, `.consent`/`.beat warm`
113-117, `.tag-mature` 276, `.sc-reset-echo`/`sc-settle` 532-537, `.beat-q` 161, `.lenbtn` 107-112;
replace the `.block`/`.check`/`.note` form grammar + the `.beat` CTA; fix `.aurora` reduced-motion 328) ·
`spikes/dreaming/lucid/web/src/App.tsx` (`data-view-heading` focus, `ReadyChip` 101 — keep calm, no second
badge) · `spikes/dreaming/lucid/web/src/components.tsx` (`.disc` precedent `EngineToggle`, `LibraryCard`
rename).

---

## 12. As-built (implemented 2026-06-23) — full §3 shipped, gates discharged

Implemented in `Start.tsx` (recomposed) + an additive `.thr-*` section appended to `theme.css`. `tsc`/
`eslint`/`vite build` all clean. **No backend, no API, no token, no card-type, no App.tsx change.** The
**full §3 direction shipped** (not just the §9-Q2 staged subset), including the cool-dawn ground and both
delight moves — because the gating contrast question was *measured and resolved*, not deferred.

**Gap #3 (aurora-behind-glass AA) — resolved by measurement, not by eyeballing.** Putting *any* glow
behind the translucent form card fails AA on the captions: `--inst-label` is tuned to barely clear (4.63:1)
on today's dark glass, so even a dim static gradient behind the card pushes it to ~3.7–4.4:1. The council
gated the aurora on "ships only if it clears AA" with the static gradient as fallback — **neither clears
behind the card.** Disposition: the dawn is anchored **behind the header zone, never behind the card**
(`position:absolute` in `.thr`, bled up behind the wordmark, masked transparent before the card, scrolls
with the page so the card can never pass under it). The card region is therefore unchanged dark glass →
captions stay 4.63:1. The §7 `.aurora` reduced-motion floor was dropped `.5`→`.44`.

**Measured AA (WCAG formula, reusing `integrations/aurora-theme/tools/check-contrast.py` math):**
header text over dawn 6.6–11.9:1 · card captions 4.63:1 · describe placeholder 5.16:1 · filled-blue CTA
ink 6.12:1 · consent-reason copper 5.76:1.

**§9 Q1 (image-seed weight):** shipped option (a) — a collapsed `<details>` disclosure ("or start from an
image") with a *styled* trigger over a hidden real input; raw native control never shows; `fileRef` +
consent-clear preserved. **§9 Q2 (scope):** shipped the full §3 (see above). **§9 Q3 (gating consults):**
both discharged — **responsible-ai-privacy-skeptic: SHIP** (contract preserved byte-for-byte; red line
standalone & unconditional; B2-gift honest; the in-page React-escaped consent card is *safer* than the old
`window.confirm`). **ui-accessibility-reviewer: SHIP-AFTER-FIX → fixes applied:** (1) the page `body`
carries its *own* `--inst-blue 8%` top-right radial, so the worst header background is **body-blue + dawn
stacked** — dawn opacity dropped `.42`→`.32` (keeps the `--inst-muted` ready-chip at 4.54:1 stacked) and
the eyebrow brightened to `mix(--inst-text 26%,--inst-label)` (5.82:1 stacked, geometry-independent);
(2) redundant `aria-checked` removed from the native `role="switch"` checkboxes. Two coexisting polite
live regions (App readiness + this status) accepted (polite serializes).

**Delight shipped:** S1 the describe field blooms the develop-aurora in place while a described opening is
in flight (honest pending state, DOM, ~0 VRAM); S2 consent **Cancel** settles "Cleared — nothing was
uploaded" via the `.sc-reset-echo`/`sc-settle` grammar and clears the staged file. Begin blooms once on
press (never idle/hover). One warm source preserved (copper = consent + Mature-on only); no earcon (a
"no audible cue here" contract is baked at the live-region site); no confetti.

**Reverting:** `git revert`/`git checkout` of the single `Start.tsx` + `theme.css` diff. No service, unit,
config, or backend touched — ADR-0005 tx N/A.
