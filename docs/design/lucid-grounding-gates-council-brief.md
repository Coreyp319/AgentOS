# Design-council brief — Lucid "grounding gates" (keeping the dream consistent with itself)

- Status: **Decided (design DIRECTION ship: write ADR-0037 + this brief) — v1 scoped to L0 canon
  ledger + L2 palette flag; deferred layers gated on measurement spikes and two absent reviewer
  voices. All seams are in `apps/dreaming/lucid/` (throwaway, excluded from the Cargo workspace),
  so this is proposal-of-a-proposal: the ledger and the flag are DESIGNED-not-built.**
- Date: 2026-06-21
- Facilitator: design-discourse-mediator (neutral; reconciles + decides, does not generate design)
- Relates to: [ADR-0014](../adr/0014-lucid-interactive-branching-dream-loop.md) (the branching loop
  this governs; §"no concurrency" / single serial lease), [ADR-0033](../adr/0033-lucid-quality-two-tier-and-identity-carry.md)
  (identity carry + GPU-measure-before-shipping discipline; its identity A/B is itself still owed),
  [ADR-0025](../adr/0025-lucid-dream-tree-and-spatial-feedforward-annotations.md) (spatial
  feedforward), ADR-0003 (fail-open supervised), ADR-0005 (reversible-by-default / apply-rollback)
- Inputs reconciled: the discourse synthesis, the round-1 rating verdict (8.39, ITERATE→finish),
  the market positioning brief, the delight & differentiation pass.
- Artifacts proposed by this brief: this brief; the [ADR-0037 stub](../adr/0037-lucid-grounding-gates.md) (§8).

---

## 0. The one-paragraph decision

**Build "grounding gates" as a TWO-LAYER v1 — scoped down from the proposed four — and write
ADR-0037 (Status: Proposed) to record it.** The two layers that ship are **L0, an accumulating
canon ledger** (a compact synopsis + flat fact-set that replaces the labels-only telephone-game
context at `lucid_linear.py:1090`) and **L2 palette, flag-only** (a zero-install cv2 histogram
check that surfaces color drift and *keeps the clip*). Both are zero-GPU-residency, fail-open to
today's behavior byte-for-byte, fully reversible, and add no new dependency. **Auto-re-roll
defaults OFF in v1** (human disposes). **L1 narrative-judge is demoted to a soft note folded into
the L0 pass** pending the absent `ai-generation-reviewer`. **L3 re-grounding / VACE is split into
its own ADR-0038** (heavy generation-time method, own VRAM spike), but its trigger contract stubs
as a node field here so L2-identity has a future consumer. **L2-identity ships later, CLIP-first
(not ArcFace-first), gated on a GPU-measurement spike** — and the proposal's ArcFace citation is
corrected (see §6). This is the smallest version that meaningfully fixes "the dream forgets /
contradicts itself": L0 fixes contradiction-from-amnesia at the root; the palette flag surfaces
the one drift a user actually sees.

---

## 1. Decided design direction

### L0 — Canon ledger (ships; the real fix)
- `chain["canon"] = {synopsis, facts:{subjects, place, time_of_day, props, mood}}`.
- Updated by **one cheap LLM pass folded into the existing `propose()`/`ground_frame` Ollama call**
  — no new narrator, no model eviction churn (honors don't-reinvent-Ollama). Written at the accept
  hook (`lucid_linear.py:1024-1025`, beside `save_chain`). Consumed in `context_for():1090`,
  **replacing** the `" -> ".join(labels)` join.
- Red-line-gated exactly like `caption`/`prompt` (`gate_prompt` at `:990` is the safety authority;
  the ledger pass is steering, never a safety gate).
- **Fail-open**: any tooling miss → today's label-chain context, byte-for-byte.
- **Ephemeral for private dreams**; **a cache of the spine, not an independent source of truth** —
  re-derived O(spine) text passes on branch/revert, never orphaned. Old chains load unchanged
  (`.get("canon")`).
- A flat fact-set, **not a scene-graph**: a 3B over-formalizes a scene-graph and generates its own
  contradictions (owner call: `design-researcher` + `generative-artist`).

### L2 — Palette gate, flag-only (ships; the first concrete keep-and-flag)
- Zero-install cv2 histogram correlation vs opening/parent frame, mirroring `lucid_facecv.py`'s
  venv-shell + **fail-closed-None** contract (`faces()` returns int or None, `:42-54` — verified).
- Emits a **structured verdict on the node**; the human-facing word is composed in the web frontend
  (i18n), never in Python.
- **Flag only — never rejects, never blocks** the ~4.5-min single-serial-lease loop. A flag is fed
  forward into L0 ("colors shifted at beat N") so drift is *recorded*, not just stamped.

### Surface contract for any flag (binding)
- A single calm `consistency` chip in the existing eyebrow row → `--st-amber`, **never `--st-red`**
  (red is reserved for the red-line safety gate). Fraunces `.tag` reason on reveal.
- **Continuous / proximity-driven, not binary**; cumulative drift shown **once at the spine, not
  per-node spam** (closes the "calm surface crying wolf" and the repeat-fire anti-pattern).
- **Non-color-redundant** (icon + word, ratified by `ui-accessibility-reviewer`).
- **Observational, intent-deferring voice** ("Colors shifted — fine if you meant it," never
  "violation/failed"); past-tense for anything the agent did.
- Click reveals reason + offers **one** undoable re-roll (the not-taken take preserved as a dim
  alternate on the tree).
- **Sonically silent** — a gate trip is neither `needs_you` nor `snag` on `agent.json`; it is an
  on-surface annotation on a panel the user is already watching. Never an earcon, never `notify-send`.

### Deferred (gated)
- **L1 narrative judge** → demoted to a soft note in the L0 pass for v1; standalone NLI-style gate
  awaits `ai-generation-reviewer`.
- **L2 identity** → CLIP-image-embedding first (reuses the adherence gate's CLIP weights; one model,
  two gates; CPU/ONNX-CPU), ArcFace a **measured upgrade** only if the spike proves CLIP can't
  separate "same person" from "drifted person."
- **L3 re-grounding / VACE** → its own **ADR-0038**; trigger contract stubs as a node field here.

---

## 2. Rating verdict + 10/10 gap plan (round 1: 8.39, no caps)

Weighted 8.39 (vision 9 · experience 8 · craft 9 · feasibility 9 · market 6). **No cap fired**
(non-negotiables clean: rides existing Ollama; reversible via the `:1013-1018` discard seam;
fail-open to today byte-for-byte; sonically silent). Verdict: **ITERATE — a finish, not a rethink.**
The latent a11y risk (amber chip contrast over an arbitrary bright video midframe) is a **gap, not
yet a cap**, because nothing renders yet — but it becomes a cap the moment a surface ships.

**The 9.0 line (do these three):**
1. **Write ADR-0037 (Proposed)** with the six contractual sentences + the honest "threshold is
   unmeasured, ships flag-only" status line. — *Owner: design-discourse-mediator. Closes vision-fit
   (behavior change → ADR is the lone open non-negotiable).* **Done by this brief (§8).**
2. **Name + cite the L0←L2 forward-feed seam** — where a palette verdict becomes a ledger fact, at
   `step():1024-1025` before `save_chain`. The one anti-drift claim with no `file:line`. — *Owner:
   design-technologist + interaction-designer. Closes craft + vision + feasibility at once.*
3. **Measure the amber chip's contrast over a worst-case bright/busy midframe**; add a scrim/plate
   token if the frame can't guarantee the floor. — *Owner: visual-systems-designer +
   ui-accessibility-reviewer. Closes the latent a11y cap-risk.*

**The 9→10 path (spikes + tests):**
4. **Ledger-prompt spike** — prove a 3B holds a stable synopsis + flat fact-set across ~5 beats
   inside the folded `propose()` pass without context bloat or self-contradiction (the one unspiked
   load-bearing assumption; `lucid-beatgen-prompt-redesign` 3B-fiddliness risk). — *Owner:
   design-technologist. Buildable now, not substrate-blocked.*
5. **L0 ledger-merge + L2 histogram-correlation as pure functions in `lucid_ground.py` with unit
   tests** to the `derive_feed` bar (pin merge precedence + correlation→verdict thresholding; pin
   branch/revert as O(spine) off-lease text passes). — *Owner: design-technologist (build),
   generative-artist (ledger-merge shape).*
6. **Palette-threshold sanity pass** — known-drift vs known-clean frame pairs to pick a threshold
   that doesn't false-positive on *intentional* cuts (cut-to-night, new location). — *Owner:
   design-technologist (fixtures) + motion-designer ("what counts as an intentional cut").*

**Beyond this ADR (gated on the two absent voices):**
7. Minimal control loop ≤2 steps (chip→click→one undoable re-roll). — *interaction-designer.*
8. "Not checking" affordance + repeat-fire ceiling (distinguish "consistency good" from
   "consistency unmeasured"; cumulative-at-spine). — *interaction-designer + content-voice-designer.*
9. Ratify the observational voice strings into the ADR. — *content-voice-designer (must-fix).*
10. Reframe positioning + name comparators in the ADR. — *content-voice-designer, informed by
    market-landscape-analyst.*

---

## 3. Market positioning

**Category: create, don't join.** Refuse the crowded "AI video consistency" shelf (where Lucid
loses every benchmark to StoryMem/Stand-In/Phantom and to ReelMind's turnkey cloud). Create the
narrow, empirically-empty category: **consistency-as-supervised-reversible-disposal on a live
creative loop** — a kill-switched, fail-open, **keep-and-flag** gate under a single serial lease,
which no generation-time *method*, post-hoc *evaluator*, or *silent corrector* in the field is.

**Position statement.** For the person dreaming on their own 4090 who watched a branching story
forget its character and drift its color three beats later, Lucid grounding gates is a
supervised-disposal layer that **keeps a drifted clip and flags it honestly** (and rebuilds "the
story so far" from a canon ledger, not a label chain) — **unlike** cloud-batch coherence or
weights-fused identity methods that can be neither flagged, reverted, nor turned off per-beat —
**because** it falls out of AgentOS non-negotiables a cloud or weights-fused competitor would have
to contradict their own architecture to copy.

**Three pillars (maturity-marked):**
1. **Keep-and-flag, never silent-correct, never block** [LEAD / moat] — built on the PROVEN
   fail-open discard seam (`:1013-1018`); a gate is a third discard reason on a proven path.
   ColorMatch *corrects silently* (Wan2.2 #172 drift documented + unfixed); a detector-that-keeps
   is calmer and more honest. DESIGNED.
2. **The dream remembers its own canon** [legible fix] — replaces `" -> ".join(labels)` with an
   editable, branch-derived text ledger. Tracks StoryMem / ConStory-Bench precedent for the *idea*
   (their bank is latent keyframes; ours is *text you can read and edit* — the durable, non-obvious
   twist). DESIGNED, zero-GPU, no new dep.
3. **Yours, local, reversible, ephemeral** [trust triad] — runs by shelling the ComfyUI venv,
   writes nothing for private dreams, discards like a lease preempt. DESIGNED, **with a VERIFY
   GATE**: "persists nothing private" must be specified and verified, not assumed — not a *category*
   claim until then.

**Market-fit: differentiation 8 / defensibility 8.** The moat is the substrate (single serial
lease + reversible chain + fail-open loop on a built discard seam), not the histogram. Local-private
is an *unwilling* gap for incumbents (it cannibalizes their data moat), not an absent one. **Cap to
watch:** until L0 + one gate ship, this is a strong roadmap on a built foundation — score the delta,
not the message; never let the brief's confidence read as present-tense product.

---

## 4. Delight & differentiation — the signature moves

### S1 (one-more-thing) — Make the canon line the dream's own voice, editable in one tap.
The deepest, most ownable differentiator: the canon ledger is *plain text the user owns*. Surface
it as a single Fraunces line in the eyebrow row — system-voice-quiet — that the user can glance at
and recognize *their own story being understood*: **"A lighthouse keeper, at dusk, on the north
cliff — searching."** One tap expands synopsis + fact chips (Fraunces `.tag` italic, the narrative
serif). It is honest and non-creepy **by construction** — the reveal isn't "it was watching," it's
"here is your story, in its own words, and you can fix a word." The one-tap correction makes
**model-proposes / code-disposes felt at the narrative layer**: the model wrote the logline, *you*
dispose of a wrong word, and that edit steers every future beat. If only one beat ships in v1, ship
this. Zero GPU, zero warm token spent (system-voice grey + serif, never warm).

### S2 (peak-end) — The re-anchor as time-travel reassurance: "you didn't lose anything."
When a beat drifts and the user clicks "re-roll from canon," the drifted take **slides into the
dim-alternate lane on the dream tree** (`.node.alt`, already styled), the re-grounded take blooms in
(existing `forming…` → poster-bloom), and a Fraunces line reads **"Kept your take — grew a fresh one
from the start."** Undo is *visible as a branch, not an erasure* — the deepest moat (ADR-0005
complete control) made felt in 10 seconds. Auto-re-roll OFF in v1 is exactly what keeps this a
delight, not an intrusion.

### Earned microdelights
- Fact chips fade in **by confidence** (certain at full weight, inferred at the `.gchoice`
  uncertainty opacity) — the user learns to read the dream's confidence at a glance.
- The drift chip is a **patina, not an alarm** — amber deepens slightly as cumulative spine distance
  grows; calibrated so a cut-to-night doesn't read as drift.
- The canon line updates with a **200ms one-word settle** (reduced-motion: appears).
- Private dreams say it once: **"This canon lives only here, and burns with the dream."**

### Restraint — the one carried hard gate
S1 and the drift chip **do not ship** until `visual-systems-designer` + `ui-accessibility-reviewer`
confirm the amber chip + serif canon line clear contrast over a worst-case bright/busy video
midframe (scrim/plate token if the frame can't guarantee the floor). Delight that fails contrast on
a bright frame is exclusion dressed up. No sound is added.

---

## 5. Recorded dissent
- **`generative-artist` (6/10)** — would ship **L0 ALONE** as ADR-0037 and split the palette gate
  (as Color-Match *prevention*) and everything else out. Objection: even a flag risks a
  "verdict-machine" feeling on a creative surface — "a creative dream is not a build pipeline." The
  decision keeps palette as **flag-only (never rejecting)** and reframes the ledger from *verdict*
  to *the dream's own voice* (S1), which partially meets this. Dissent recorded, not erased; routed
  into the calm-surface constraints (§1) and S1's authorial-not-diagnostic register.
- **`content-voice-designer` (6/10)** — not an architecture dissent; conditions the score on a
  ratified voice contract being written into the ADR. Treated as a **must-fix** (gap item 9), not a
  dissent.
- No lane dissents from L0-first, flag-don't-block, or auto-re-roll-OFF-in-v1.

---

## 6. Corrections to the room's shared citations (verified against live code)
- Discard-fail-open path is **`lucid_linear.py:1013-1018`** (clip None at 1013, superseded at 1016).
- Commit / `save_chain` at **`:1024-1025`**.
- `context_for` labels-only at **`:1090`** (`labels = [n["label"] …]`), joined `" -> "` at `:1096`.
- Red-line `gate_prompt` at **`:990`**.
- `lucid_facecv.faces()` is fail-closed-None (int or None) at **`:42-54`** — exactly as cited.
- `_with_subject(_subject_for(...))` identity-prefix (ADR-0033) at **`:989`**, gated at `:990`.
- **Citation fix:** the Face Consistency Benchmark (arXiv 2505.11425) uses the **DeepFace model
  zoo**, NOT "SCRFD+ArcFace cosine" — that is insightface lore, not the paper's finding. Correct
  this in the deferred-identity-layer brief before any rejecting threshold ships.

---

## 7. Open questions for the human
- **Q1 — Auto-re-roll posture beyond v1.** (a) stays OFF / manual permanently; (b) add a per-dream
  **Loose/Steady** toggle (default Loose) so a user opts into one budgeted auto-re-roll on
  identity/palette. Cost of (b): one setting + the ~9-min two-beat turn when it fires.
  **Recommendation: ship (a) in v1; revisit (b) after the palette flag-surface UX proves out and
  the identity spike lands** — don't build an aggression dial for a gate that can't yet measure
  meaningfully.
- **Q2 — Two missing voices gate the deferred layers.** `ai-generation-reviewer` owns the L1
  narrative-judge / ledger-prompt shape (the room deferred to a voice not in the room).
  `responsible-ai-privacy-skeptic` must rule on (a) the L1 reconcile button being non-coercive,
  (b) the no-verdict-persisted-for-private-sessions guarantee, (c) whether auto-re-roll needs
  one-time consent. **Recommendation: L0 + palette-flag v1 proceeds now (no rejecting gate, no
  agency gesture); gate the deferred layers on these two consults.** I do not fill empty lanes by
  ruling for them.

---

## 8. Artifact: ADR-0037 stub
Drafted at [`docs/adr/0037-lucid-grounding-gates.md`](../adr/0037-lucid-grounding-gates.md),
Status **Proposed**, scoped to L0 + L2-palette-flag v1, carrying the six contractual sentences
(voice; sonically-silent; ledger-as-spine-cache + private-ephemeral; L1-demoted-to-soft-note;
L3-trigger-stub + ADR-0038 split; unmeasured-threshold honesty line) and the contrast hard-gate.
