# Design-council brief — Lucid "choice moment" + eager-glimpse generator

- Status: **Decided (composition SHIP) + Proposed (generator amendment, gated)**
- Date: 2026-06-19
- Facilitator: design-discourse-mediator (neutral; reconciles, does not generate)
- Relates to: [ADR-0014](../adr/0014-lucid-interactive-branching-dream-loop.md) (the loop;
  §3 "No concurrency, ever"), [ADR-0023](../adr/0023-creative-environment-pipeline.md)
  (creative-environment pipeline — amendment target), ADR-0003 (fail-open supervised),
  ADR-0004 (graphics yield = kill/relaunch, not live shedding), ADR-0005 (apply/rollback /
  reversible-by-default), ADR-0010/0018 (VRAM admission + coexistence partition)
- Inputs reconciled: the discourse synthesis, the round-1 rating verdict, the market
  positioning brief, the delight & differentiation pass.
- Artifacts proposed by this brief: this brief; the ADR-0023 amendment stub (§9).

---

## 0. The one-paragraph decision

**Ship the IA reframe and the on-stage gutter composition now** (no ADR, no substrate
change) with four corrections. **Hold the eager-glimpse generator at `Proposed`** — the
substrate is kill-not-yield on a single 24 GB 4090 where VRAM *is* the constraint, so the
spike's headline ("eager render 2+ glimpses, every beat, during playback") **structurally
cannot fire** and was rejected unanimously on verified-code grounds. The honest, zero-GPU
**still conditioning-frame is the default and the whole shippable feature**; the motion
glimpse is an opportunistic, lease-gated, on-intent *maybe* that earns its ADR amendment
only after a four-number measurement gate and a `responsible-ai-privacy-skeptic` consult.

---

## 1. Decided design direction

### 1a. SHIP NOW — no ADR, no substrate (rated 9–10 on its own)

1. **The IA reframe (already shipped, ratified).** Start-as-hero; readiness collapsed to an
   ambient `ready` chip (`App.tsx:75` mounts `ReadinessCard` only on `!can_dream`); engine
   picker demoted to a `<details>` disclosure with plain-language glosses (`Balanced ~17GB` /
   `Sharper, heavier ~22GB`); Burn/Delete moved into the active-dream state (a real
   "burn nothing" bug fix). Files: `web/src/App.tsx`, `components.tsx`, `theme.css`.

2. **The on-stage gutter composition.** Choices bloom into the empty 16:10 side gutters
   beside the 9:16 portrait clip — **never over the image**; the dream-tree demotes to pure
   history/branch-map. Connector lines reuse the dream-tree's existing future-edge grammar
   (`Chain.tsx:447-458`). Three corrections, all owner-assigned:
   - **(a)** the connector edges **draw in once toward the viewer (~600ms), then settle to a
     static hairline** — kill the infinite `@keyframes flow` (seven agents flagged it as a
     forever attention-magnet). Owner: motion-designer.
   - **(b)** the dwell is real `<video>.pause()` on `ended` + **frozen last frame** + a slow
     ~10s ambient breath on the `.spill`/vignette at the **Hills warm-breath cadence** — never
     a re-`loop` (a Wan beat's last frame ≠ first frame, so `loop` hard-cuts every dwell). The
     CSS-fake slow-mo in the spike (`animation-duration:52s`) hid this. Owner: motion-designer,
     consult generative-artist on the spill grammar.
   - **(c)** the **default card thumb is the zero-GPU still conditioning-frame + label/prompt**;
     the spinner is removed; a dashed still is a **legitimate resting state, not a failure**
     (no apology copy). Owners: interaction-designer (state model) + content-voice-designer
     (the two-state copy).

3. **Honest copy + a non-color ready tell + `aria-live`.** Eyebrow `a glimpse — not the final
   beat` (short: `glimpse`); in-flight `a glimpse of this path`; reclaimed `Pick to see this
   one`; gutter fail-open line in the existing skip-banner voice (`Chain.tsx:433`). The ready
   state changes a **non-color channel** (eyebrow word-shift to `this path`, doubling as the
   screen-reader truth) and announces via the `aria-live` slot already present at `App.tsx:64`.
   Owners: content-voice-designer + interaction-designer; ratify with `ui-accessibility-reviewer`.

### 1b. HOLD AT `Proposed` — the eager-glimpse generator (ADR-0023 amendment)

The **fidelity choice is sound** (same warm i2v model + same seed + same start-frame + same
prompt, min-length `MIN_LEN=17` / Wan 4k+1 stride, reduced steps, last-frame differentiates —
an *honest prefix* of the real beat; no t2i model-switch thrash). **Honesty: passed by
construction.** What fails as-specified is the *policy*:

- **"The GPU is idle during playback" is FALSE on this substrate** (verified). `est_mib()`
  floors at **17000 MiB** per beat (`lucid_engine.py:130-135`); the code comment notes only
  ~20 GB is free on a live desktop and Q6 is **admission-refused mid-use** (`:96-99`);
  generation is **single-lease, serial**; clip-end hands the GPU to the VLM beat-gen pass
  (`force_evict`, `qwen2.5vl:3b`, `keep_alive:0`). The window is *mid-handoff*, not idle. The
  spike's "idle" is a mocked `setTimeout` (`choice-moment.html:235-239`).
- **The cheap axis is wrong for the binding resource.** Frame-count/steps is cheap for
  *wall-time*, not *VRAM*: a 17-frame Wan render loads the same ~15–17 GB of weights as a
  49-frame one. So "2+ cheap glimpses" are **2+ full ~17 GB admissions** that cannot
  co-reside with each other or the real beat. They serialize (ADR-0014 §3 "No concurrency,
  ever" makes this a hard substrate fact, not a tuning choice).

**The recommended generator contract (the amendment body):**
- Glimpse rendering is **opportunistic, lease-gated, never a promise**. Default is the still;
  the **motion glimpse is one render at a time, on focus/dwell-intent, lowest lease tier,
  first-SIGKILLed under contention, cancel-on-pick/blur, fail-open to the still**.
- Speculation windows the **open-ended dwell**, not the 1–5s playback (you cannot hide minutes
  of render in seconds of playback).
- **Confidence-rank** branches by the LLM beat order; glimpse top-1 first, top-2 only if VRAM
  stays free and intent is shown.
- **Off-by-default / conservative, user-toggleable, with a one-line disclosure** (local-first/
  consent; the user pays the electricity and lives next to the fan).
- **Sensory clause:** any future dwell earcon is off-by-default and singular; the
  **audio-suppression predicate off `agent.json` `state`** is mandatory (a chime must never land
  on a VRAM-yield flicker); glimpse-ready and pick are silent forever.
- **Chrome stays instrument-blue (`--inst-blue`); warm (`--brand-warm` `#E0884F`) stays reserved**
  system-wide for the wallpaper's `needs_you` cue.

---

## 2. Tensions and how they resolved (owner · tie-break)

| # | Tension | Owner lane | Resolution (tie-break) |
|---|---|---|---|
| T1 | eager-2+ vs one-lazy vs zero-render default | design-technologist / `resource-safety-reviewer` | Still-default (zero GPU) ∪ one-on-intent enhancement; eager-2+ **rejected**. (performant/yield-aware ADR-0004; fail-open ADR-0003) |
| T2 | render-ahead in playback vs dwell | interaction-designer | Window the **dwell**, not playback — strictly better, resolves the timing math |
| T3 | slow-mo "loop" physically wrong for i2v | motion-designer | `pause()` + frozen last frame + ~10s spill breath; never re-loop. (calm & honest) |
| T4 | spinner + "rendering…" placeholder | content-voice + brand-identity + interaction | Drop spinner; dashed still is a resting state; "not arrived" ≠ "reclaimed" are two states. (calm & honest) |
| T5 | color-only ready signal | `ui-accessibility-reviewer` (ratify) | Add non-color channel (eyebrow word / tick) + `aria-live`. (accessible) |
| T6 | speculate at all on a single-user box? | `responsible-ai-privacy-skeptic` **(absent — must consult)** + sound-designer | Off-by-default + disclosure; still-default makes the tool fully usable at zero speculation. (local-first/consent) |
| T7 | warm `#E0884F` glimpse chrome | brand-identity-designer | Chrome stays instrument-blue; warm reserved. (already true in spike) |
| T8 | dwell earcon + audio-suppression | sound-designer | Earcon = proposal-of-a-proposal; **suppression predicate off `state` is the load-bearing deliverable**. |

---

## 3. Rating verdict + 10/10 gap plan

**Split-mode. SHIP the IA reframe + composition (Tier A) now; ITERATE the generator.**
Weighted overall **8.25/10** (Vision 9 · Experience 7 · Craft 9 · Feasibility 8 · Market-fit 8).
- **Vision-fit cap does NOT fire** — the capping move (eager-2+) was caught and removed before
  it could land; the recommended direction violates no non-negotiable.
- **Experience a11y cap is ARMED at 5, NOT firing** — the running spike signals "ready" by
  border color only (`choice-moment.html:88-90`). It does not fire because we score a *proposal*
  whose Decision mandates the non-color tell, and the reveal itself is clip-end/keyboard
  reachable. **It converts to a live 5-cap the instant the choice-moment ports to the real app
  without T5.** Binding precondition, not a deduction.

**Prioritized gap plan to 10/10 (owner = a maker, never a rater):**

*Tier A — ships now (no ADR, no substrate):*
1. Port spinner→still-default + dashed-as-resting-state. (Experience/Craft/Vision) —
   interaction-designer + content-voice-designer.
2. Non-color ready tell + `aria-live` on dwell/bloom. (Experience; **disarms the a11y cap**) —
   interaction-designer, ratify `ui-accessibility-reviewer`.
3. Settle the connector edge (draw-in once, then static). (Experience/Craft) — motion-designer.
4. Real `<video>.pause()` + frozen last frame + ~10s spill breath at Hills cadence.
   (Vision/Experience/Craft) — motion-designer, consult generative-artist.

*Tier B — the measurement gate (decides whether the motion glimpse exists at all):*
5. **The cancel→VRAM-free spike (pass/fail, the make-or-break):** start a glimpse render,
   SIGKILL mid-flight, measure free-ms + whether the next real-beat admission stalls.
   (Feasibility/Craft/Vision) — design-technologist, lease verdict `resource-safety-reviewer`.
   *If free isn't prompt, the speculative tier is infeasible-as-specified and the still-default
   IS the whole feature (a clean 9–10).*
6. **The honest-prefix divergence diff (gate d):** render one beat full vs 17-frame/reduced-step
   (same seed/frame/prompt), diff the last frames against a named threshold. If it diverges, copy
   softens to "where this *might* go." (Market-fit 8→9.5 if it holds) — design-technologist /
   content-voice-designer.

*Tier C — the ADR amendment body (substrate-blocked; cannot land until Tier B returns + consults):*
7. Specify `Tier::Speculative` in the priority value-space + write the one kill metric as the
   gating test (lowest tier, never holds an arbitration lock, never delays a `force_evict`).
   — design-technologist, ratify `resource-safety-reviewer`. Blocked on #5 + #9.
8. Pin the VRAM estimate to active-quant + fix the stale `_est_mib()` docstring
   (the "~22 GB" desync vs the 19000 the function returns). — design-technologist.
9. **`responsible-ai-privacy-skeptic` consult** on off-by-default + disclosure (consent/heat;
   fan-noise-as-uninvited-earcon). **The amendment cannot leave `Proposed` without this.**

**Top-3 next moves, in order:** (1) run the cancel→VRAM-free spike (#5); (2) port
spinner→still-default + non-color tell + `aria-live` (#1, #2); (3) honest-prefix diff (#6) +
land the privacy-skeptic consult (#9).

---

## 4. Market positioning (significantly-better-than-market)

**Position:** AgentOS is the **resource-and-safety floor under your local AI** (not an OS, not
an orchestrator — Hermes is the brain) that **coordinates the GPU so the agent never OOMs your
desktop and lets you revert what it changes.** Category decision: **create "local AI resource
substrate," do not join the "agentic OS" shelf** — Segment F (VRAM graphics-vs-LLM arbitration
on one consumer card) has **no found competitor**.

**Lucid tip:** *the only local-first interactive dream-video tool whose branches preview
themselves in the dream's own idle window and yield the card the instant you need it —
buildable only because the arbitration substrate exists underneath.*

**Maturity honesty (state every time, or raters dock for vaporware):**
- PROVEN: `lease`/`coord` arbitration + SIGKILL preempt + cgroup reclaim; `feed.rs` read-only
  producer; NVML pressure math; Lucid last-frame i2v chaining.
- DESIGNED (roadmap-with-proof-gate): apply/rollback tx (ADR-0005); the Lucid speculative
  preview — **and as-specced it overclaims** the concurrency the substrate forbids. Corrected
  here to **serial-in-idle-window, cancel-on-pick, fail-open** — true, on-brand, uniquely
  enabled by cancel-on-preempt.

**Current-work position score 6.5/10** (the shipped substrate is a 9; the artifact under review
overclaims). **Delta-to-10 is execution-maturity, not message:** ship the DESIGNED tx + the
serial-preview through the real lease; always ship the VRAM denominator ("against a 17–21 GB
model") and the kill/relaunch honesty (~1.5 GB / ~800 ms flicker).

**Anti-persona ("not for you if…"):** you want <100 ms real-time video (that's cloud
Decart/Genie — Lucid is contemplative-by-design); a chatbot that acts out of the box (the
actuation layer is roadmap; `state:3 acting` is reserved-but-unemitted); multiple GPUs (the
whole wedge is the single-contended-card collision); Nix-grade atomic rollback (this is live
per-op desktop-ledger, not boot-generation snapshots).

---

## 5. Signature delight moves (the few that make it *ours* — all zero-GPU, all survive a kill)

- **S1 — The dwell *breath*: the dream doesn't pause, it inhales.** On clip-end the last frame
  holds and the `.spill`/vignette swells through one slow ~10s breath at the **Hills
  warm-breath cadence** — the same rate the reactive wallpaper exhales on task-complete. The
  desktop and the dream breathe as one organism; unforgeable because one substrate authors both.
  Zero-GPU (CSS on a frozen `<video>` frame), so it lands identically on a contended 4090 and a
  fresh login. The antithesis of the spinner: *suspended, alive, waiting on you* — not buffering.
- **S2 — The branch you didn't take is remembered, never wasted (the differentiation peak / the
  "one more thing").** On pick, the unpicked options' connectors retract into the demoted
  dream-tree and seed faint un-grown ghost-nodes at the head (reusing the `alt`/dashed grammar,
  `Chain.tsx:464`). *Nothing you considered is lost* — ADR-0005 reversibility made **generative**:
  not "undo a mistake" but "the path you set down is still there, one click from blooming." Zero
  speculative GPU (a label + the on-disk conditioning still). **Build this before the motion
  glimpse — it is the delight that doesn't need the measurement gate.**
- **Earned microdelights:** the single directional connector draw (the dream *reaching out*,
  once); the eyebrow word *earning* the border (state legible as language = the SR truth); a
  faint `every path starts here` dwell label turning the technical fact (i2v frame-0 ≈
  conditioning frame) into a felt poem.

**Restraint check:** no second warm source (S1 reuses the *cadence*, not the reserved hue —
chrome stays `--inst-blue`); no earcon by default; no confetti, streak, or countdown. Every
signature beat is zero-GPU and survives `prefers-reduced-motion` and a substrate kill — by
design, because the speculative render cannot be relied on.

---

## 6. Recorded dissent (never erased)

- **The room is unanimous against eager-2+-glimpse-every-beat as written** — on verified-code
  grounds (single-lease serial, `est_mib()` floor), not taste. This is the rare case where the
  ensemble stands against the user-chosen policy *as specified*.
- **Residual lane-level split on the default render tier:** design-technologist (Option A) and
  visual-systems-designer (Proposal A) would ship **zero** speculative motion render until
  measured; art-director, motion-designer, interaction-designer, generative-artist,
  design-researcher accept **one** lazy/confidence-ranked glimpse on intent. The Decision folds
  both (still-default ∪ one-on-intent) so neither is overridden; the measurement gate (#5)
  decides whether even the one-on-intent glimpse is admitted.
- **sound-designer** dissents from any future second earcon and from un-capped eager previews on
  **acoustic** grounds (fan noise as an uninvited earcon); folded into the off-by-default decision.

---

## 7. Missing voice (named, not decided-for)

**`responsible-ai-privacy-skeptic` was not in the room.** The consent/heat posture for rendering
branches the user won't take, on a box they live next to, is **its lane** — I do not fill an empty
lane. The amendment cannot move from `Proposed` until this consult lands (gap #9).

---

## 8. Accepted tradeoffs

- Glimpses are **frequently absent** (the dashed still is the common state on a busy desktop) —
  correct and honest, not degradation.
- The motion glimpse fires **only on intent / when VRAM is loose** — we give up "all branches
  moving at the instant of choice" to keep "all branches are truthful start-frames, instantly,
  free."
- We **ship the composition before the generator amendment settles** — the visual lands now; the
  pipeline change earns its ADR with data.

---

## 9. ADR amendment stub (Proposed — gated)

> **ADR-0023 amendment — Lucid speculative-glimpse lease tier**
> - Status: **Proposed** — blocked on (a) the cancel→VRAM-free measurement gate and (b) the
>   `responsible-ai-privacy-skeptic` consent/heat consult.
> - Change: introduce a **`Tier::Speculative`** glimpse-render class — lowest priority, never holds
>   an arbitration lock, never delays a `force_evict`/preemption (reuse the ADR-0023 "wind tick
>   takes no lock" guarantee), cancel-on-pick/blur, fail-open to the still conditioning-frame.
> - Policy: dwell-windowed (not playback), confidence-ranked top-1-first, one render at a time,
>   off-by-default + one-line disclosure.
> - The one kill metric (gating test): *a glimpse must never delay the real beat's admission, nor
>   cause the real beat / wallpaper / interactive lease to OOM or be preempted.* No metric → not
>   shipped.
> - Sensory clause: audio-suppression predicate off `agent.json` `state` is mandatory; any dwell
>   earcon is off-by-default and singular.
> - Generation contract: same warm i2v model + seed + start-frame + prompt, `MIN_LEN`/Wan-stride,
>   reduced steps; the final beat is an independent full render so glimpse cost never degrades final
>   quality. Pin the VRAM estimate to active-quant; fix the stale `_est_mib()` docstring.

The full ADR text is for code + the human to dispose; this brief proposes, it does not ratify.

---

## 10. Open questions for the human (options + a recommendation)

1. **Does the motion glimpse survive the measurement gate at all, or is the still-frame default
   the whole feature?**
   - (a) still-default + one-on-intent glimpse if measurements clear (richer; costs building a new
     lowest-tier speculative lease class + the ADR amendment).
   - (b) still-default only (cheaper, fully honest, ships sooner).
   - **Recommendation: ship (b) now, promote to (a) only after design-technologist /
     `resource-safety-reviewer` return the four numbers** — matches ADR-0023's own
     measure-before-architecture discipline and gives you the composition immediately.

2. **Consent/heat for rendering branches you won't take, on your own single-user box.**
   - (a) off-by-default toggle + one-line disclosure; (b) on-by-default (the original proposal).
   - **Recommendation: (a), pending the `responsible-ai-privacy-skeptic` consult** — local-first/
     consent is a tie-breaker and the fan-noise-as-uninvited-earcon argument is real on a machine
     you also live on. I will not decide this empty lane.
