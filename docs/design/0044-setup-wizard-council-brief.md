# Design-Council Brief — AgentOS Setup Wizard (ADR-0044)

**Status:** Final council synthesis, Round 2 (mediator-ratified). Advisory until code + human dispose.
**Surface:** `integrations/setup/` — localhost web installer (port 9125, never tailnet-served).
**Mediator:** design-discourse-mediator. **Date:** 2026-06-23.
**Verdict carried forward:** capped **4/10 → ITERATE** (uncapped weighted ~7.0). One live cap (a11y).
Target 10.

---

## 0. What was decided

The setup wizard gets a first-time user from nothing to a first reviewable result on the rig they
already own, on a strong, honest spine — but it **does not ship at its current score**: one
accessibility cap (T0) holds the overall at 4 regardless of the 7.0 weighted average. This brief
ratifies the rating-aggregator's central correction, reconciles all four upstream lanes (design
direction · rating + gap plan · market positioning · delight), and gives the caps-first ordering
to 10.

**The one cross-lane conflict I had to adjudicate — and how it resolves.** The carried design
direction filed **T1 (the mature-video consent gap) as a binding cap** ("a non-consenting caller is
silently upgraded to mature in executable code"). The rating-aggregator, on a source read, demoted
it to an **honesty deduction**. I read the source myself before ratifying, because this flip decides
the verdict:

- `setup_web.py:82` — the **web** surface (the work under review) **refuses**:
  `if (mature or b.get("rating") == "mature") and not mature: return None, "mature affirmation required"`.
  The wizard only sends `mature:true` after the `confirm()` at `wizard.html:204`. **The web wizard
  cannot upgrade a non-consenting caller.** Confirmed.
- `setup.py:526-531` — the **CLI** path's gate (`is_mature and not yes → return 3`) **fires on every
  mature path** unless `--yes` is passed; in `express`, `--yes` *is* the affirmation to run express.
  The "silently upgraded in executable code" framing is **not supported by source** — the gate is
  never skipped.

**Ruling: T1 is a high-priority honesty DEDUCTION, not a cap.** I ratify the aggregator. The one live
cap is **T0 (accessibility)**. The verdict is **ITERATE**, not RECONSIDER. The carried direction's
five-of-five "convergence" on the cap was two raters (vision-fit, market-fit) inheriting the
direction's misread of `_cmd_express` without tracing the `:527` gate — a double-counted misread,
exactly the "averaging away / inflating a cap without checking the fix removes a violation" pitfall.

**What IS real and must still be fixed (deduction, not cap):** the copy↔data honesty drift.
`registry.json:312` makes `video-10eros` `rating:"mature"`, yet `README.md:28` markets it "Account
needed? No" and `README.md:31` says "Text and image need no sign-up at all" while listing video-10eros
in the same no-account table. "No account" is true on the *token* axis (free HF mirror) but reads as
"no gate" — and there is **no SFW video lane at all** (both video bundles `mature`, `registry.json:312,329`).
That is a genuine *honest-mapping* defect on the brand's own honesty wedge.

---

## 1. Participants (input reconciled, by exact name)

**Design team:** art-director, motion-designer, visual-systems-designer, interaction-designer,
design-technologist, generative-artist, sound-designer, brand-identity-designer,
content-voice-designer, design-researcher.
**Aggregators/synthesizers:** rating-aggregator, market-positioning-synthesizer.
**Reviewers routed (named, sign-offs pending — not yet ruled):** ui-accessibility-reviewer (blocking,
T0), responsible-ai-privacy-skeptic (T1 wording + axis split), reversibility-tx-reviewer (T2 refcount),
resource-safety-reviewer (keyring 0600 + coexistence-blind fit), ai-product-reviewer (front-door framing
+ SFW-video spike), ambient-embodiment-reviewer (completion-toast default), rater-feasibility (ADR
§106 drift).

---

## 2. Agreements (the strong spine — preserve verbatim, do not touch this pass)

Confirmed at source; all four lanes concur these are why this is a recoverable ITERATE, not a redesign:

- **Brownfield adopt-in-place is the moat** (`setup.py:117-123` `_ollama_has`, `:274-285`
  `artifact_present` scans `~/ComfyUI/models/**` and refuses to re-fetch). Structurally un-followable:
  an incumbent model-manager would have to stop being the library to copy it. **This is the lead.**
- **Security posture** (`rater-craft` 9): token-via-stdin never argv (`setup.py:336,562-573`),
  `curl --config -` keeps the Authorization header off `/proc` (`:357-366`), loopback triple-guard
  (`setup_web.py:176-182`), port 9125 absent from the tailnet, CSRF/`Sec-Fetch-Site`, atomic
  `.part`→rename, `deadman44`/minor/real-person research denylist. **Keep untouched.**
- **Text-first ordering** (`_cmd_express:594-614`): text → ComfyUI → image → video; the local text
  model then writes the opening prompt. The architectural thesis is right.
- **Calm-by-default:** all-clear is quiet (`✓ ready`, no celebration); failures degrade to plain text.

---

## 3. Tensions — adjudicated, caps first

| # | Conflict | Owner lane | Resolution (tie-break) |
|---|---|---|---|
| **T0** | **CAP.** Zero `aria-live` on `#progress`/bundle-state spans → a 25-min download finishes silently for a screen-reader user; zero `prefers-reduced-motion`/`-transparency`; the `tight` rung has no non-color channel. (Corrected: `--faint #878c9b` measures 4.7–5.47:1, **passes AA** — this is NOT a contrast cap.) | interaction-designer (ARIA) + visual-systems-designer (glyph/token) | **Caps overall at 4** via the *accessible* non-negotiable. A sub-20-line clear, not a rethink. Blocking sign-off: **ui-accessibility-reviewer**. |
| **T1** | **DEDUCTION (was mis-filed as cap).** `video-10eros` is `rating:"mature"` (`registry.json:312`) but README:28/31 market it as no-account/no-sign-up; no SFW video lane exists. "No account" reads as "no gate." | content-voice-designer (copy) + ADR owner (axis) | **Honest-mapping** deduction, not a consent bypass (web refuses at `setup_web.py:82`; CLI gate fires at `setup.py:527`). Strike-and-clarify the copy + split `rating` (capability) from `fetch_gate` (account). Sign-off: **responsible-ai-privacy-skeptic**. |
| **T2** | Reversibility floor: ADR §152-155 promises manifest-based Remove of weights+keyring; only `keyring_clear` ships (`setup.py:559`) — no manifest writer, no weight-uninstall. | interaction-designer + design-technologist | **Reversible-by-default (#1)**. Minimum owed: write the inverse manifest per fetch + read-only "what's stored" audit + "Forget token". Per-bundle weight-Remove **defers a phase, refcount-gated** (shared artifacts `b2-vision`/`narrator`/`t2i-opening` recur, `registry.json:316-338`). Gate: **reversibility-tx-reviewer**. |
| **T3** | Warm grammar collision: `blue→warm` gradient spent on logo (`wizard.html:24`), "Make one" button (`:49`), every progress bar (`:52`), and the resting 18+ tag (`:39`) — warm (`#e0884f`, the desktop's *only* "needs-you" hue) burned as decoration before the desktop where warm must mean "I need you." | brand-identity-designer + visual-systems-designer + motion-designer | **Calm & honest ambient mapping**. De-warm all in-progress/decorative surfaces; reserve warm for the one ready-beat (see §6 S2). Taste-bounded, non-blocking. |
| **T4** | Accent lineage: wizard uses Lucid instrument violet `--blue:#9b82e0` (`wizard.html:11`); art-director wants the shipped desktop Aurora `#765CC4`. | art-director (dissent) / visual-systems-designer | Derive a shared token (`instrument-tokens.css`), re-gate `check-contrast.py`, then choose. **Frozen until T0/T1 clear.** |
| **T5** | Completion signal: a 25-min unattended download finishes with no ambient ping. | sound-designer / ambient-embodiment-reviewer | swaync completion/failure toast, default-on-provisional gated by `AGENTOS_SETUP_NOTIFY`. **ambient-embodiment-reviewer** owns the default. Non-blocking. |

---

## 4. Decision — the prioritized path to 10/10 (caps-first is the only valid order)

The overall **cannot exceed 4 and the surface does not ship** until Tier 0 clears.

### TIER 0 — remove the live cap (gates everything)
1. **Lift the a11y cap (T0).** `aria-live="polite"` on `#progress` + bundle-state spans (announce
   start/ready/fail once); `@media (prefers-reduced-motion)` → `transition:none` on `.bar > i` and kill
   the sheen; `prefers-reduced-transparency` → Lucid's opaque-glass fallback for `backdrop-filter:blur(14px)`;
   give `tight` a non-color shape glyph (◐) to match `too-big`'s ⚠; re-run `check-contrast.py` against
   the *blurred* glass bg to confirm AA holds. Owner **interaction-designer** + **visual-systems-designer**;
   blocking sign-off **ui-accessibility-reviewer**.

### TIER 1 — honesty + correctness floors (this round, below the cap)
2. **Reconcile the SFW-video copy↔data drift (T1) — strike-and-clarify.** README:28 / `desc` at
   `registry.json:314` "No" → **"No token — 18+ affirmation required"** (preserves the brownfield wedge
   *and* is honest); README:31 stop implying video-10eros is sign-up-free. Split `rating` (capability)
   from a new `fetch_gate` (account) axis so the four surfaces read one field and can't re-drift. Owner
   **content-voice-designer** + ADR owner; sign-off **responsible-ai-privacy-skeptic**.
3. **Fix the research argv bug.** `setup.py:262` passes `"--allowedTools", "WebSearch", "WebFetch"` —
   `WebFetch` lands as a stray positional and is dropped. Join to one arg `"WebSearch WebFetch"`. Broken
   on first use. Owner **design-technologist**.
4. **Reconcile the ADR §106 `systemd-run`-worker drift.** ADR §106 says every fetch delegates "via the
   **same `systemd-run --user` worker**" as ADR-0043; shipped `setup_web.py:95` spawns `setup.py` directly
   via `subprocess.Popen`, no confinement. The divergence is defensible (the installer can't self-confine)
   but is **silent ADR drift** — a non-negotiable. Amend §106 to match reality. Owner ADR owner /
   **design-technologist**; sign-off **responsible-ai-privacy-skeptic** (touches safety posture).
5. **Reversibility minimum (T2).** Inverse manifest per fetch + read-only "what's stored" audit +
   "Forget token" wired to `keyring_clear` (`setup.py:559`, CLI-only today). Per-bundle weight-Remove
   defers, refcount-gated. Owner **interaction-designer** + **design-technologist**; gate
   **reversibility-tx-reviewer**.
6. **Keyring `0600` fallback — implement or strike.** `keyring_set:332` returns `False` when `secret-tool`
   is absent; ADR §133 promises a disclosed `0600 O_CREAT|O_EXCL` file. Implement it or strike the promise.
   Low real-world bite (`secret-tool` is on-box) but a headless-install dead-end. Owner **design-technologist**;
   ruling **resource-safety-reviewer**.
7. **Replace the three native dialogs with in-register UI.** `confirm()` ComfyUI (`wizard.html:191`) +
   18+ affirmation (`:204`), `prompt()` research modality (`:195`), `alert()` errors (`:207`) → the 18+
   becomes an inline expand-in-place panel with red-line copy verbatim + model-card link + checkbox-then-
   button (the ADR's "distinct screen"); research → three modality buttons; errors → inline `.err` rows.
   This is the one credibility seam in an otherwise-polished glass UI. Owner **interaction-designer** +
   **content-voice-designer**.

### TIER 2 — thesis + market wedge
8. **Surface the text-first payoff / lease-gated handoff (the market wedge).** `GET /api/suggest_prompt?modality=`
   → the just-installed local model writes the opening prompt on the ready card + copy button, deep-linked
   into Lucid's real `POST /api/start` as `?prompt=`. Confirmed absent from the web path
   (`suggest_opening_prompt` is CLI-only, `setup.py:612`). Text/image unblocked now; video honesty depends
   on #2; an SFW-video bundle (#2 path A) is **substrate-blocked: unspiked** (see §9). Owner
   **design-technologist** + **interaction-designer**; spike + validation **ai-product-reviewer**.
9. **Honest, coexistence-aware fit copy.** Soften "we only offer models that fit your GPU" → "we flag what
   may not fit"; `too-big` → "too big for this GPU — find a smaller one ↗"; `vram_gb===0`+smi →
   "Couldn't read your GPU". Note `bundle_fit:228-240` is isolation-only/coexistence-blind (ignores the
   ~2.5GB the wallpaper/keyhole/warm-pool already hold) and ADR-0004's measured 19.5GB-for-18GB-reported
   reality — near the boundary the label *will* mislead, so the verb is **"warn," never "guarantee."**
   Owner **content-voice-designer** + **design-technologist**; route **resource-safety-reviewer**.

### TIER 3 — ambient discipline (taste-bounded, non-blocking; do not score until T0 clears)
10. Warm reservation (T3, see §6 S2) + completion/failure swaync toast (T5) + `instrument-tokens.css`
    token extraction (de-risks T4). Owners **visual-systems-designer** / **brand-identity-designer** /
    **sound-designer**; toast default is **ambient-embodiment-reviewer**'s call.

**Projected trajectory:** clear #1 → cap lifts → ~7.0 ITERATE; #2–#4 → ~7.5; #5–#7 → ~8.5; #8 (handoff
wired) + #9 + the §6 delight moves → 9–10.

---

## 5. Market position — significantly-better-than-market

**Position statement.**
> For the Linux creator who already runs Ollama and ComfyUI, AgentOS's setup wizard is **the on-ramp to
> the reactive AgentOS desktop that adopts the weights you already have in place and downloads only the
> gap** — unlike StabilityMatrix, Pinokio, and LM Studio, which make you start over in *their* centralized
> model store — because adopting read-only is something a model-manager structurally can't do without
> ceasing to be the library it's built to be.

**Category decision (mediator-ratified, ai-product-reviewer to confirm):** **refuse "AI model
installer/manager"; frame as "the brownfield-respecting front door to a reactive desktop."** On feature
*breadth* (dedup, version-pin, browse UI) the wizard loses by design (ADR Non-goals, §159) — the narrow
scope IS the position. The non-goal ("not a model manager") is the moat: AgentOS can read your model dir
in place *because* it declines to own the library.

**Beachhead (locked):** the local-AI creator on a single prosumer GPU who already has an Ollama + ComfyUI
rig — the box AgentOS ships on, and the user *every* incumbent serves worst (all want migration into a new
store). The pain is concrete and at an all-time high: the realistic stack is ~150–200 GB; re-downloading
what you already have is hours and a quota.

**Three pillars (sourced + maturity-tagged):**
1. **"Adopts what you already have. Downloads only the gap." [PROVEN — the lead, structurally
   un-followable]** — `setup.py:117-123,274-285`.
2. **"Your tokens stay in your OS keyring — never a file, never the tailnet." [PROVEN, privacy-gated]** —
   `setup.py:336-366`, `setup_web.py:176-182`. Sell as a *provable trust floor*, not a category-win;
   weekend-copyable, so it is the floor, not the moat. Hold the category claim until
   responsible-ai-privacy-skeptic confirms child-env token lifetime + the 0600 fallback.
3. **"All three modalities, sequenced text-first so your local model writes your first prompt." [PROVEN
   ordering; the first *sample* is a deep-link, ROADMAP]** — `_cmd_express:594-614`, `suggest_opening_prompt`.

**The seam IS the moat, not any one tile.** Fit-verdict (LM Studio), Civitai/HF download (Swarm/SM),
NSFW-default-off (Swarm), keyring (gh) each exist *separately* in a rival; none combines them, because each
is anchored to one segment. Build every head-to-head on the seam, not the tiles.

**Forbidden claims (carry verbatim to expression lanes):**
1. "We know it fits" → **"we warn before you waste a 24 GB download."**
2. "Auto-makes your first creation" → it **deep-links** you to make it (the handoff is `Make one ↗`,
   `wizard.html:124`, not a fired `POST /api/start`; auto-fire is roadmap).
3. "Reversible removal / undo the install" → **cut entirely** — the manifest-based remove path does not
   exist in shipped code (only `keyring_clear`). Restore only when T2/#5 ships.
4. Any privacy *category-win* before responsible-ai-privacy-skeptic signs off.
5. Do **not** fold the VRAM-yield/lease story in — the wizard is disk/network I/O only (ADR §160-162).

**Market-fit score: 8/10** — unusually high for an AgentOS surface because the wedge is *shipped code,
verified*, and the lead claim is the one feature no rival has and *structurally* can't copy. The path to
10 is "ship the ROADMAP items (remove-manifest + auto-sample handoff) and tighten the fit math," **not**
rewrite the message — the message is sound and honest as constrained above.

---

## 6. Signature delight moves (the elevation on a cleared bar)

Every move below is calm · accessible · reversible · honest · zero-VRAM, and assumes the warm-channel
grammar must survive intact onto the desktop the wizard hands off to. Delight by *subtraction*, not
flourish.

**S1 — The reuse ledger: count back the hours you didn't lose.** One calm, ownable line at the top of
"What do you want to make?": **"Found 84 GB already here — we'll only fetch the 19 GB you're missing."**
The wedge made felt: AgentOS *respected what you already built* instead of demanding a migration. The peak
isn't a download finishing; it's the download that never had to happen. One still line, in the T0
`aria-live` region (announced once on detect), round *down* the "found" GB so it never over-claims, anchor
to the directory ("in your ComfyUI folder") so it reads as recognized, not surveilled. Owners
**content-voice-designer** + **design-technologist**; wording cross-check **responsible-ai-privacy-skeptic**.

**S2 — The honest exhale: the only warmth in the wizard is the moment it's ready for you.** Strip warm from
every in-progress/decorative surface (logo `:24`, "Make one" `:49`, bar fill `:52` → cool violet; resting
18+ tag `:39` → neutral/amber outline). Reserve warm for exactly one beat: when a bundle crosses to ready,
the `✓ ready` row gets a single slow 600ms warm settle that blooms once and rests cool — never loops. The
wizard *teaches the warm grammar before the desktop ever uses it*: the first warm a user feels means "this
is done, it's yours" — so the desktop's later warm for "I need you" lands pre-learned, no tutorial.
`prefers-reduced-motion` → the warm tint is just present as a still on the ready row; the `✓` glyph carries
it for color-blind users. Owners **motion-designer** + **brand-identity-designer** + **visual-systems-designer**.
This is the *delightful form* of T3 — not a separate ask.

**Earned microdelights:**
- **The first prompt as a quiet local gift** (this is gap-item #8 and the market wedge, same code path):
  when a bundle goes ready, reveal the model-written opening prompt in muted text — *Your text model
  suggests: "a lone lighthouse in fog, slow drift"* — and carry it into Lucid as `?prompt=`. A cloud
  onboarding can't hand you a prompt your own machine authored. The "suggests" framing must read as a
  proposal (model proposes, user disposes).
- **The detect-sweep, once.** On first `/api/state`, let "Your machine" + bundle cards resolve top-to-bottom
  in a single 200ms stagger, then perfect stillness. Reduced-motion → instant. The contrast with the
  subsequent silence is what registers (idle is sacred).
- **Ready is a noun, not a cheer.** Change a ready row's size line to **"ready — nothing left to fetch"** —
  naming the adoption thesis one more quiet time at the peak. Pure copy.
- **The "what's stored" audit as reassurance, not a chore.** When T2/#5 lands, surface a calm footer —
  *"Everything this added is listed and removable."* — *before* the 80 GB download, so the big download
  feels safe. Until the remove path ships, the line reads only "Everything this added is listed" (true the
  moment the manifest writer lands; do not claim "removable" before then — forbidden-claim #3).

---

## 7. Accepted tradeoffs

- The overall **stays at 4 and the surface does not ship** until T0 clears — a cap is rethink-until-gone,
  not average-it-away. The weighted 7.0 is irrelevant while it binds.
- Full reversible weight-Remove **defers a phase** — accepted *only because* the manifest + audit +
  token-forget land now, so the create-half no longer ships without a revert-half.
- The page goes more monochrome-violet; "Make one" loses its warm gradient — accepted: warm must re-earn
  salience so the desktop's later warm bloom reads as signal.
- The front-door framing **costs reach** — not sold as a general model manager, won't win the "manage 200
  models across 5 UIs" user. Correct: the narrow scope is the position.
- If the Apache-Wan SFW video lane can't be validated this round, the headline SFW-video claim is **struck,
  not delivered** (the honest fallback).

---

## 8. Recorded dissent (never erased)

- **art-director (T4):** the front door should wear the shipped desktop Aurora violet `#765CC4`, not the
  Lucid instrument `#9b82e0` (`wizard.html:11`). Escalated, **frozen until T0/T1 clear.**
- **art-director / generative-artist (T3):** would keep warm on the resting 18+ tag as the "needs-a-decision"
  use. The decision (brand-identity-designer + the §6 S2 delight pass) moves warm to the affirmation/ready
  *act* and gives the resting 18+ tag a neutral/amber outline, on consent + scarcity grounds. Recorded;
  mediator sides with moving warm off the resting tag.
- **sound-designer (T5):** holds completion-toast should be default-on; provisional pending
  ambient-embodiment-reviewer. Recorded.
- **Prior facilitator vs aggregator (T1):** the prior round filed T1 as a binding consent cap. The
  rating-aggregator (and this mediator, on a source read) ruled it an honesty **deduction** —
  `setup_web.py:82` refuses, `setup.py:527` gate fires. **This brief adopts the deduction filing; verdict
  ITERATE, not RECONSIDER.** The cap claim was a double-counted misread of the CLI `express`/`--yes`
  semantics by vision-fit + market-fit. The only residue is the narrow human sub-choice in §9.

---

## 9. Open questions for the human (options + cost + recommendation)

1. **SFW video bundle vs strike the claim (T1 — the one unspiked product fact).** Whether an ungated
   Apache-Wan i2v lane yields an acceptable SFW first clip on the 4090 is unverifiable from artifacts.
   **(a)** add + validate a real `rating:"sfw"` video bundle (delivers the ADR headline; needs a
   design-technologist spike + ai-product-reviewer validation); **(b)** strike "Account needed? No" /
   "no sign-up" from README:28,31 + `registry.json:314` desc + ADR. **Recommendation: ship (b) strike
   immediately to clear the honesty deduction and stay honest, and run the (a) spike in parallel — restore
   the claim only when validated.**
2. **Aurora accent lineage (T4).** **(a)** adopt desktop Aurora `#765CC4` (front-door-matches-house);
   **(b)** keep Lucid instrument `#9b82e0` (matches the next screen); **(c)** derive a shared token,
   switchable in one tx. **Recommendation: (c) then (a)** — extract `instrument-tokens.css` now (required
   for the re-hue discipline anyway), re-gate `check-contrast.py`. **Frozen until T0/T1 clear.**
3. **Completion toast default (T5).** Default-on (long unattended download) vs default-off (calm purist).
   **Recommendation: default-on gated by `AGENTOS_SETUP_NOTIFY`, pending ambient-embodiment-reviewer.**

**Missing voices to consult before landing:** ui-accessibility-reviewer (blocking — T0),
responsible-ai-privacy-skeptic (blocking — T1 wording + axis split + privacy category-claim),
reversibility-tx-reviewer (T2 refcount), resource-safety-reviewer (keyring 0600 + coexistence-blind fit),
ai-product-reviewer (front-door framing confirm + SFW-video spike), ambient-embodiment-reviewer (T5 default).

---

## 10. Artifacts

- **This brief** → `docs/design/0044-setup-wizard-council-brief.md`.
- **ADR amendments to** `docs/adr/0044-onboarding-brownfield-bundles.md` (corrections to 0044's own
  promises — amendments, not new behavior): reconcile the SFW-video claim vs registry data (§ table /
  README), the §106 `systemd-run` vs direct-`subprocess.Popen` drift, the §133 keyring `0600` fallback
  (implement or strike), the §152-155 reversible-removal claim (manifest + audit + token-forget land now;
  weight-Remove defers).
- **New short ADR stub** *only if* the human approves the SFW video bundle + the `fetch_gate` field (net-new
  behavior). Stub drafted below.

---

## ADR stub (draft — only if the human approves net-new behavior)

```
# ADR-00XX: Setup-wizard capability/account axis split + reversible install manifest

Status: Proposed (stub — pending design-discourse-mediator brief 0044 §10 + human approval)
Supersedes/amends: ADR-0044

## Context
ADR-0044's registry conflates two independent axes on one `rating` field: model *capability*
(sfw|mature, the 18+ red line) and *account gate* (token required or not). README markets
`video-10eros` as "Account needed? No" (true on the account axis) while it is `rating:"mature"`
(the capability axis), so "no account" reads as "no gate." Separately, ADR-0044 §152-155 promises
reversible Remove of fetched weights + keyring tokens, but no manifest writer or weight-uninstall
ships — the create-half exists without a revert-half, violating ADR-0005 (reversible by default).

## Decision
1. Split `rating` (capability: sfw|mature) from a new `fetch_gate` (account: none|civitai|hf) field.
   All four surfaces (registry, README, wizard, ADR) read one field per axis; copy is generated, not
   hand-written, so it cannot re-drift.
2. The wizard writes an inverse manifest per fetch (bundle id → artifact paths + bytes + refcount) on
   every successful download, and exposes a read-only "what's stored" audit + a "Forget token" action
   (wired to the existing keyring_clear). Per-bundle weight-Remove is refcount-gated (shared artifacts
   must not be unlinked while another bundle references them) and may land a phase later.
3. (Optional, human-gated) Add a real `rating:"sfw"` / `fetch_gate:"none"` Apache-Wan i2v video bundle,
   validated on the 4090 by ai-product-reviewer, ordered ahead of the mature video bundles — restoring
   the headline "first video, no account" claim honestly. If unvalidated, the claim is struck, not shipped.

## Consequences
- Honest-mapping restored: no surface can claim "no gate" for a mature bundle.
- Reversible-by-default restored for the wizard: nothing is downloaded that cannot be listed and (phased)
  removed.
- Cost: a registry schema migration (one field), a manifest writer (~10 lines), and a refcount pass before
  any destructive Remove. The SFW-video lane is gated on a spike and may not land this round.
```
