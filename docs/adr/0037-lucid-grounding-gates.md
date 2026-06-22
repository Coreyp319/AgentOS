# ADR-0037: Lucid — grounding gates (keeping the unfolding dream consistent with itself)

- Status: **Proposed** — DESIGNED-not-built. All seams live in `spikes/dreaming/lucid/` (throwaway,
  excluded from the Cargo workspace), so this is proposal-of-a-proposal. **v1 scope = L0 canon
  ledger + L2 palette flag only.** The palette threshold is **unmeasured**; it ships **flag-only**
  (never rejecting) until a GPU/fixture sanity pass calibrates it — mirroring ADR-0033's honesty
  (its identity half shipped measured, its A/B still owed).
- Date: 2026-06-21
- Decider: design-discourse-mediator (council), human disposes.
- Builds on (does not supersede): [ADR-0014](0014-lucid-interactive-branching-dream-loop.md) (the
  branching loop; single serial VRAM lease, ~17 GB floor, ~4.5 min/beat — "no concurrency"),
  [ADR-0033](0033-lucid-quality-two-tier-and-identity-carry.md) (identity carry + GPU-measure-
  before-shipping discipline), [ADR-0025](0025-lucid-dream-tree-and-spatial-feedforward-annotations.md)
  (spatial feedforward).
- Honors: ADR-0003 (fail-open supervised — every layer degrades to today's behavior byte-for-byte),
  ADR-0005 (reversible-by-default — a rejected clip discards like a lease preempt, chain untouched).
- Splits out: **ADR-0038** (L3 re-grounding / VACE — a heavy generation-time method, own VRAM spike).

## Context — the complaint

Lucid is a branching i2v "dream": the LLM proposes the next beat from the current frame, ComfyUI
renders ~4.5 min/clip, the last frame becomes the next anchor. Today "the narrative thus far" is
two thin, open-loop threads:

- **Textual context** — premise + a chain of 2–5-word beat LABELS + a one-sentence VLM caption of
  the current frame. Verified: `context_for()` joins `labels = [n["label"] …]` with `" -> "`
  (`lucid_linear.py:1090`/`:1096`). No accumulating facts, no full prompts. This is literally the
  telephone game.
- **Visual anchor** — the parent clip's last frame + a one-time subject text descriptor scraped
  from the opening frame (ADR-0033 `_with_subject` at `:989`, gated at `:990`) + a deterministic
  per-node seed.

Nothing measures a returned clip or a proposed beat. Web-documented failure modes: Wan i2v
reinvents the face across cuts; palette/color drift across last-frame chaining (Wan2.2 #172);
telephone-game degradation; the LLM proposes beats that contradict canon because only labels
survive into context.

## Decision

Build **grounding gates** — "model proposes, code disposes" applied to the creative loop — as a
**two-layer v1**, scoped down from the proposed four, with the heavy half (L3) split to ADR-0038.
Every layer is **kill-switched, fail-open, deterministic, reversible, bounded**.

### L0 — Canon ledger (the real fix)
- `chain["canon"] = {synopsis, facts:{subjects, place, time_of_day, props, mood}}` — a compact
  synopsis + **flat fact-set** (NOT a scene-graph; a 3B over-formalizes a graph and generates its
  own contradictions).
- **Update lane (corrected by the ai-generation-reviewer fold, 2026-06-21):** the proposed "fold
  into the existing `propose()` call" was wrong — `propose()` runs `NARRATOR_MODEL` (hermes3:3b,
  text-only) at `BEAT_TEMP=0.78` (the *surprise* lane) and fires at menu-roll time, **before** the
  user has chosen the beat the ledger needs. A ledger update is a fidelity task. So: the update rides
  the **0.6 fidelity lane on the vision model** (`MODEL`=qwen2.5vl:3b) — folded into the **next
  turn's `ground_frame` pass** (`lucid_linear.py:608`), which is already warm, already on the
  fidelity lane, already sees the new tip frame, and already red-line-gates its output (one beat
  late, genuinely free). If a one-beat-late ledger is unacceptable for the S1 surface, the fallback
  is a **dedicated 0.6 call at accept time sequenced BEFORE `force_evict` (`:772`)** (~3s cold load,
  hidden in the dwell). **Never on `propose()`/`BEAT_TEMP`.** No new narrator (don't-reinvent-Ollama).
- **IO contract — DELTA not merged-ledger (must-fix):** the model RETURNS a bounded delta, **code
  merges**. A 3B asked to re-emit the whole ledger resamples and drifts every stable field; a delta
  scoped to "what changed this beat" is the task a 3B can do, and makes merge precedence
  code-disposed (model proposes the delta; code disposes the canon). Delta schema:
  `{synopsis_suffix(≤120c|""), facts_add:{props[],subjects[]}, facts_change:{<key>:{to,evidence}}, drift_note(≤80c|"")}`.
- **Code-disposal contract (`lucid_ground.py`, pure + unit-tested to the `derive_feed`/`validate_beats` bar):**
  parse-fail / wrong-type → drop that field, keep prior (never coerce, never raise); `facts_change`
  requires non-empty `evidence` or is discarded; **`subjects` is append-only** — the ledger may NOT
  *change* a subject (identity is ADR-0033's job, captured once from the opening frame); single-valued
  keys (`place`/`time_of_day`/`mood`) replace, evidence-gated; `facts_add` append-dedup-cap (per-field
  hard caps; over-cap drops new, never evicts stable); **empty delta = clean no-op** (never persist a
  degraded result over a good ledger — the `propose` "don't seal a transient []" precedent).
- **Synopsis bound + re-derive-as-primary (must-fix):** the ledger feeds `context_for`, whose output
  feeds the next ledger update — an unbounded self-append loop that re-introduces the telephone game.
  So **re-derivation from the spine `{labels + captions}` is the PRIMARY per-turn update** (input =
  the ~5-node spine, structurally immune to the feedback loop), not just the branch/revert repair;
  any retained synopsis is hard-capped keep-tail (≤~600c).
- Red-line-gated like `caption`/`prompt` — **`S.red_line_ok()` on every ledger string before persist
  AND before feed-back** (`gate_prompt` at `:990` is the only safety authority; the ledger pass is
  steering; injection markers refused, since ledger strings uniquely loop back into a system turn).
- **Invariant — ledger is a cache of the spine, not an independent source of truth:** re-derived
  O(spine) **off-lease text passes**; never orphaned. Old chains load unchanged (`.get("canon")`,
  additive node/dict fields only). **Canon is a chain FIELD, never a sidecar file** (so the existing
  `burn`/`purge_persistent` cover deletion with zero new code).
- **Invariant — private dreams persist nothing UNENCRYPTED (privacy must-fix):** the canon is written
  **only** by mutating the in-memory `chain` dict and letting `step()`'s single `is_current()`-guarded
  `save_chain` persist it (mirror the `_subject_for` precedent, `:673-692`) — which routes private →
  tmpfs (burned) and persistent → deletable cache. No `lucid_ground.py` function writes a chain /
  canon / synopsis to a path of its own. The *only* durable private form is the ADR-0028 encrypted
  stash, on explicit opt-in (and the synopsis is NOT hoisted into the stash index row). Logs carry
  metadata only — never synopsis/fact content.
- **Fail-open:** any tooling miss → today's label-chain context, byte-for-byte.
- **Open Q the reviewer raised:** does L0 need an LLM at all? A zero-LLM code-accumulated ledger
  (bounded `labels + running captions`) may pass the spike's bar — the spike MUST A/B the LLM ledger
  against it and ship the LLM only if it measurably wins (don't reach for a model where `derive_feed`
  would do).
- **MEASURED — the gating spike answers the open Q: a HYBRID, not all-LLM and not all-code**
  (`spike_canon_ledger.py`, hermes3:3b @0.6, 6 runs, 2026-06-21; the deterministic merge has 22/22
  offline selftests). The LLM-vs-code A/B split cleanly by fact:
  - **LLM delta owns who/what/story:** subject-retention **1.00**, new-subject capture **0.92**,
    hallucination **0.00**, valid-delta **0.97** — the zero-LLM control is **0.00** on subjects AND
    entrances (a regex cannot do identity/entrances). The LLM clears every gated bar here.
  - **Code owns when/feel:** the deterministic time_of_day/mood keyword extractor tracks changes
    **1.00** every run; the 3B's own change-tracking is borderline (**0.83**, run-variance — it
    occasionally mis-formats `set`).
  - **`place` is DEMOTED to best-effort, ungated:** unreliable on BOTH a 3B (files "north cliff" under
    props) and a naive regex (grabs "oilskin coat"). Least-valuable fact; not worth a bigger model.
  - **So v1 L0 is a hybrid:** deterministic extractors for `time_of_day`/`mood`, the LLM delta for
    `subjects`/entrances/`synopsis`, `place` best-effort — all under the proven merge. This keeps the
    small-model eviction economics and uses the 3B only where it measurably wins. (Three prior prompt
    iterations were required and are documented in the spike: nested→flat schema; example-values→
    all-empty schema, b/c the 3B echoed placeholder strings as canon; +tolerant coercion/grounding/
    dedup b/c the 3B emits bare-string list/set types.)
  - **GATE CLEARED on-box 2026-06-22 (`--full --runs 20`, both models).** The **production LLM half
    (`qwen2.5vl:3b`, the model the `ground_frame` vision pass uses) is a clean GO**: subj_ret **0.97**,
    enters_rate **0.90**, halluc **0.00**, valid **1.00**, change_tracked **0.97**, place 0.85
    (best-effort). `hermes3:3b` is NO-GO *only* on `change_tracked` (0.62) — but that bar measures
    pure-LLM when/feel, which the **hybrid replaces with the code extractor (code-A/B change_tracked
    = 1.00)**; its who/what bars pass (subj_ret 0.93, enters 0.75). So the **shipped hybrid config
    (qwen delta for who/what + code for when/feel) clears every bar**: subj_ret 0.97 · enters 0.90 ·
    change_tracked 1.00 (code) · halluc 0.00 · valid 1.00. A/B stays decisive — the zero-LLM control is
    0.00 on subjects AND entrances; the LLM beats it **+0.90** on new-subject capture. Lane 0.78 was
    equal/worse as expected (subj_ret −0.02). **The N=20 enters_rate 0.90 (vs the polluted pre-fix run)
    is owed to three dispose tightenings added 2026-06-22** (see below).
  - **Three deterministic tightenings (2026-06-22, in `lucid_ground.py` + the spike), found by the
    on-box smoke** — the *production* model `qwen2.5vl` failed differently from the `hermes3` the prior
    verdict was measured on: (1) **scenery→props reroute** — a whole-phrase-scenery candidate
    (weather/light/water/plant noun + modifiers) is demoted from `subjects` (who) to `props` (things),
    so `fog`/`mist`/`vine`/`clouds` stop squatting the 4-subject cap and *starving a real character*
    (`cat` was being dropped over-cap); reroute-not-drop keeps the entrance captured (`enters_rate`
    counts subjects OR props). (2) **grounding ratio 0.5→0.6** — rejects a 2-of-4-token garbled compound
    (`notable-thall-sailing-ship`) the lenient guard let through. (3) **modifier-only reject** — a bare
    adjective (`thick`, split off "thick fog") names nothing and is never a subject/prop. Selftests
    26/26 (spike) + 23/23 (`lucid_ground`). Residual (documented, no v1 fix): an *object* with a real
    noun mislabeled as a subject (`oilskin coat`) — needs an animacy classifier, out of scope; cosmetic
    (doesn't starve, subj_ret still passes).

### L2 — Palette gate, flag-only
- Zero-install cv2 histogram correlation vs opening/parent frame, in a new `lucid_ground.py` that
  shells the ComfyUI venv exactly like `lucid_facecv.py`, with its **fail-closed-None** contract
  (`faces()` returns int or None, `:42-54`).
- **No-leak contract (privacy must-fix):** `lucid_ground.py` **reads** frame paths, computes the
  correlation in the child, returns a number/enum verdict to stdout, and **writes NO file** (mirror
  `faces()` — no debug image, no scratch copy). The L2 frame of a private dream is itself private; any
  future derived image must route through `ST.frame_ref(session, private, …)` / tmpfs so `burn` reaches
  it — never `cv2.imwrite` to a bare path.
- Emits a **structured verdict on the node**; the human-facing word is composed in the web frontend
  (i18n), never in Python.
- **Flag only — never rejects, never blocks** the single-serial-lease loop.

### The forward-feed seam (the anti-drift mechanism — name it, don't float it)
A palette verdict becomes a **ledger fact** ("colors shifted at beat N") at/beside the accept hook
`step():1024-1025`, **before** `save_chain`. This is what arrests drift: L0 *records* what L2
*detects*, and surfaces it as a **cumulative, continuous drift signal at the spine**, not N
independent per-node stamps. (Gates are a thermometer; L0 is the thermostat.)

### Surface contract (binding)
1. A tripped gate is a **calm `consistency` chip** → `--st-amber`, **never `--st-red`** (red is the
   red-line safety gate only). Fraunces `.tag` reason on reveal.
2. **Continuous / proximity-driven, not binary**; cumulative drift shown **once at the spine**, not
   per-node spam (no repeat-fire nagging).
3. **Non-color-redundant** (icon + word). Contrast measured over a worst-case bright/busy video
   midframe before shipping; scrim/plate token if the frame can't guarantee the floor (HARD GATE).
4. **Observational, intent-deferring voice** ("Colors shifted — fine if you meant it," never
   "violation/failed"); past-tense for anything the agent did.
5. **Grounding gates are sonically silent** — a gate trip is neither `needs_you` nor `snag` on
   `agent.json`; it is an on-surface annotation on a panel the user is already watching. Never an
   earcon, never `notify-send`.
6. **Auto-re-roll defaults OFF in v1** — the flag suggests; the human triggers. If ever enabled it
   is capped at ONE per beat, `is_current()`-guarded (`:1016`), and narrated as a visible, undoable
   "re-anchored" tell.

### Deferred (gated)
- **L1 narrative judge → DESIGN-ONLY in v1 (hardened by the ai-generation-reviewer fold).** The
  council demoted it to "a soft note in the L0 pass," but the reviewer's call is sharper: a soft note
  is the *same* unreliable 3B signal self-auditing in the same low-temp breath — no more trustworthy
  than a separate judge, just cheaper. A contradiction *accusation* on a creative surface needs
  precision a 3B lacks (the `generative-artist` "verdict-machine" dissent). So in v1 the `drift_note`
  field is **recorded into the ledger as telemetry only — it drives NO user-facing chip and NO
  auto-action.** A standalone NLI-style judge stays fully deferred until a fidelity-validated larger
  judge exists; owner `ai-generation-reviewer`.
- **L2 identity → CLIP-image-embedding first** (reuses the adherence gate's CLIP weights — one
  model, two gates — CPU/ONNX-CPU). **NOT ArcFace-first:** insightface+onnxruntime+buffalo_l
  (~300 MB) warms a CUDA context *inside* the ~17 GB lease window, post-render, when VRAM is
  tightest — it can OOM the very render it validates (performant/yield-aware). ArcFace is a measured
  upgrade only if a spike proves CLIP cannot separate "same person" from "drifted person."
  **Citation fix:** the Face Consistency Benchmark (arXiv 2505.11425) uses the DeepFace model zoo,
  NOT "SCRFD+ArcFace cosine."
- **L3 re-grounding / VACE → ADR-0038** (heavy generation-time method, own VRAM spike). Its
  **trigger contract stubs as a node field here** so L2-identity has a future consumer.

## Why (tie-breaks, in non-negotiable priority order)
- **Reversible-by-default + model-proposes/code-disposes** → auto-re-roll OFF: a silent
  agent-initiated discard-and-re-render moves disposition from the user (ADR-0005 complete control).
- **Don't-reinvent-Ollama** → L0 rides the existing 0.6 fidelity-lane vision pass (`ground_frame`),
  no new narrator (NOT `propose()`/`BEAT_TEMP` — see L0).
- **Performant/yield-aware** → CLIP-first identity, no +300 MB CUDA dep on the critical render path.
- **Calm & honest ambient mapping** → continuous-at-the-spine (calmer AND more honest than
  binary-per-node); amber-never-red; sonically silent; "not checking" ≠ "checking, all good."

## Reviews folded in (the two absent voices, 2026-06-21)

Both deferred reviewer seats (council §7 Q2) were run on this draft. Their must-fixes are folded into
L0/L1/L2 above; the cross-cutting rulings:

### responsible-ai-privacy-skeptic — verdict: SHIP-AFTER-FIX
The canon ledger is the **most privacy-sensitive new artifact in the project** — a durable, searchable,
natural-language summary of dream content (subjects/place/mood/synopsis), arguably more sensitive than
the frames. The private-ephemeral guarantee is *architecturally achievable* (single tmpfs sink via
`save_chain`, all-sinks `burn`, encrypted-only stash) but **aspirational until tested**. Blockers,
folded above + here:
- **[Blocker] `/api/state` egress.** `lucid_web.py:516-529` `state()` returns the raw chain verbatim
  (only a `"private"` boolean), and `lucid_web` is served over Tailscale to the phone. A private
  `chain["canon"]` would leave the on-box UI to any tailnet device. **Decision owed + must be made,
  not defaulted:** strip `canon` from the `state()` serializer (expose only the one S1 display line),
  OR explicitly rule canon is no more exposed than the already-shipping `prompt`/`caption` (it isn't —
  it's a structured summary). Hand the outbound-over-Tailscale lane to `channels-integration-reviewer`;
  the "private content must not leave the device" ruling is the skeptic's: **it must not.**
- **[Blocker→fixed-in-design] single-sink + no-content-in-logs** → folded into L0's private invariant.
- **[High] saved canon rides ADR-0028 encrypted stash only** (`lucid_stash.py:240` tars the whole
  chain → canon sealed as ciphertext on opt-in save; `open_into` restores it). Restate the guarantee
  as "persists nothing **unencrypted**; the only durable form is the opt-in encrypted stash"; keep the
  synopsis OUT of the plaintext-equivalent stash *index* row (`:268`). Crypto construction → already
  reviewed for ADR-0028 (no new primitive); confirm with `security-reviewer`.
- **[High] deletion** burns canon for free **because canon is a chain field, not a sidecar** — locked
  in above; add a delete-test asserting no `canon*` residue.
- **[Medium] field contract is CLOSED** — `{synopsis, facts:{subjects,place,time_of_day,props,mood}}`
  + the drift-fact and nothing else without a new ADR; store **cumulative drift STATE, not an
  append-only per-beat event log** (a diary is a richer profile than any single field).
- **Rulings on the three Qs:** (b) the no-persist-for-private guarantee is *real only once* the
  single-sink test + `/api/state` decision + unencrypted-restatement land. (a) the L1 reconcile must
  be **OFF by default**, click-to-reveal, amber, sonically silent, undoable-as-a-branch — the v1
  design-only demotion is autonomy-correct. (c) any future auto-re-roll needs **explicit, per-dream,
  one-time, revocable consent** (a sentence, never inferred from a config flag); OFF in v1 is correct,
  recommend permanent-manual.

### ai-generation-reviewer — verdict: HOLD-FOR-SPIKE, build split-not-folded
The load-bearing L0 assumption is both unspiked and mis-architected as drafted; its must-fixes are
folded into L0 above (delta-not-merge; off-BEAT_TEMP onto the 0.6 fidelity/next-turn-`ground_frame`
fold; re-derive-O(spine) as the primary update + synopsis bound; semantic validation beyond
JSON-parse; subjects append-only; empty-delta no-op; L1 design-only; **and an A/B of the LLM ledger
vs a zero-LLM code-accumulated ledger** — ship the LLM only if it wins). Strengths credited: the
fail-open-for-steering / fail-closed-for-safety split is correctly drawn, the flat-fact-set
(not scene-graph) is right, and "cache of the spine, re-derive on branch/revert" is the correct
architecture (promoted here to the *primary* update model).

## Consequences
- v1 **detects** palette drift but does not **prevent/arrest** it (prevention is L3/ADR-0038).
  Accepted: L0 is the real drift-arrester.
- We ship a consistency surface **without the identity number** (ADR-0033's identity A/B is itself
  owed) — honest maturity, not a shipped consistency guarantee.
- One new per-dream concept (the ledger) + one new node field (the verdict) — additive, `.get()`-read,
  old chains load unchanged.
- We forgo the richer scene-graph world-state — a 3B populates it unreliably.

## Open / owed before Accepted
- **Ledger-prompt spike go-bar — CLEARED on-box 2026-06-22 (`--full --runs 20`, both models).** The
  shipped hybrid (`qwen2.5vl` delta for who/what + code extractor for when/feel) clears every bar:
  subj_ret 0.97 · enters 0.90 · change_tracked 1.00 (code) · halluc 0.00 · valid 1.00 · lane 0.78
  equal/worse as expected · A/B decisive (+0.90 new-subject over the zero-LLM control). Required three
  dispose tightenings the smoke surfaced (scenery→props reroute, grounding 0.5→0.6, modifier-only
  reject — see L0 §MEASURED). **L0 does NOT fall back to the code-only accumulator** (that arm scores
  0.00 on subjects/entrances).
- **Privacy tests (BLOCK the build until green):** (1) single-sink — a private session's canon appears
  in the tmpfs chain and in NO file under the dreams cache / ComfyUI output / any temp dir; (2) after
  `burn`, no `canon*` residue anywhere; (3) the ledger pass logs metadata only (no synopsis/fact text);
  (4) the L2 child writes no file for a private frame; (5) `save_session` seals canon as ciphertext and
  does NOT add the synopsis to the stash index row.
- **`/api/state` canon-egress decision — DECIDED 2026-06-22: STRIP canon entirely from the serializer**
  (Corey's call). `state()` never returns `canon`, for any session — canon stays pure internal steering
  on the server (zero egress/residue risk, fully reversible; no consumer exists today). Implemented at
  integration as a 1-line shallow-copy redaction (`{k:v for k,v in chain.items() if k!="canon"}`, never
  mutating the live chain). When the S1 "editable canon voice" surface is actually built it gets its own
  deliberate endpoint that serves a private dream's line from the in-memory session only.
- **Privacy tests — scoped 2026-06-22.** Two satisfied BY DESIGN (confirmed pre-wiring): `lucid_ground`
  logs no content (its only prints are the cv2 child emitting one JSON number), and the L2 child writes
  no file (reads paths, prints the number). The rest are AT-INTEGRATION (need canon actually written via
  `step()`/`save_chain`): single-sink private-ephemeral, no `canon*` after `burn`, stash seals canon as
  ciphertext and keeps the synopsis out of the index row.
- **Palette-threshold sanity pass — DONE 2026-06-22 (`calibrate_palette.py`, CPU-only on the 30 on-disk
  beat-frames). NEGATIVE RESULT: keep L2 OFF; the raw HSV-histogram metric is not discriminative on real
  dream content.** A dream's OWN beat-to-beat palette correlation (vs-parent: median 0.31, p10 0.03;
  vs-opening: median 0.20) spans the whole [0,0.94] range and overlaps the cross-dream "unrelated palettes"
  floor (median ~0) — i.e. normal i2v evolution changes the palette about as much as two *unrelated* dreams
  differ. There is no threshold that separates "steady" from "shifted": at 0.50 it flags 53% of a dream's
  own normal beats; even at 0.20, 27%. So L2-as-wired (flag when corr < threshold, vs-parent) would be a
  high-false-positive "consistency" chip. (Cross-dream is also contaminated — many dreams share the
  synthetic placeholder opening → spurious 1.0 pairs.) **Decision: `LUCID_PALETTE` stays default-OFF.** A
  useful L2 needs a rework, not a threshold: a perceptual/semantic similarity signal (not a raw color
  histogram), and/or comparison scoped to the subject/anchor region rather than the whole frame, and/or
  honest reframing as informational-only — or drop L2 from v1 and let L0 carry the grounding. (L0 is the
  real fix and is unaffected.)
- **Contrast measurement** of the amber chip + serif canon line over a worst-case midframe (HARD
  GATE on the surface shipping).
- **Reviews discharged:** `ai-generation-reviewer` (HOLD-FOR-SPIKE) + `responsible-ai-privacy-skeptic`
  (SHIP-AFTER-FIX) folded in 2026-06-21. Remaining hand-offs: `channels-integration-reviewer`
  (`/api/state` over Tailscale), `security-reviewer` (canon-in-stash crypto — no new primitive),
  `reversibility-tx-reviewer` (corrupt/rejected-ledger reverts cleanly), `resource-safety-reviewer`
  (next-turn-`ground_frame` fold is truly residency-free vs `force_evict` at `:772`).
- **BUILT 2026-06-21 (pure, NOT wired):** `lucid_ground.py` holds the deterministic cores —
  `merge_canon` (the dispose), the hybrid `extract_time_of_day`/`extract_mood` + `update_canon`
  orchestrator (code owns when/feel, the LLM delta owns who/what/story — the LLM's time/mood are
  stripped at the boundary), `canon_to_context` (the line that replaces the labels join), and the L2
  `palette_drift`/`palette_verdict` (cv2-in-venv, fail-closed-None, writes no file). 19/19 unit tests
  (`test_lucid_ground_canon.py`) + live cv2 check (identical→steady, red/blue→shifted, missing→unknown).
- **WIRED into the live server 2026-06-22 (uncommitted — `lucid_linear.py`/`lucid_web.py` carry heavy
  parallel WIP, so a clean path-scoped commit is owed; behind kill-switches, fail-open):**
  - L0: `_canon_for(chain, node, caption)` folds a PER-NODE canon (cached on `node["canon"]`, like
    `beats`/`caption`, so a branch carries its own spine's canon — re-derive-O(spine) by construction) in
    `roll_menu` after the caption is grounded+red-line-checked; `context_for` takes the fresh canon and
    `canon_to_context` REPLACES the labels join (fail-open to labels). Kill-switch `LUCID_CANON` (default on).
  - L2: `step()` stores a flag-only `node["palette"]` verdict after `extract_last_frame`. Kill-switch
    `LUCID_PALETTE` **default OFF** (the gate is uncalibrated + its cv2 child runs in the leased turn —
    opt in post-calibration); child timeout cut 30s→`LUCID_PALETTE_TIMEOUT_S` (10s).
  - Egress: `lucid_web.state()` runs `_strip_canon` (RECURSIVE — chain, every node, and any nested `prev`
    backup; copies, never mutates the live chain) so canon never reaches the phone (the §5.2 decision).
  - **3-lens focused review 2026-06-22 (privacy SHIP / determinism ITERATE / resource ITERATE — no
    Blocker) + must-fixes APPLIED:** (1) [resource HIGH] the per-roll canon LLM call now passes a
    free-VRAM headroom gate (`CANON_HEADROOM_MIB`, skip-the-load-under-the-warm-lease → fail-open to
    labels) so it can't OOM additively on a resident ComfyUI; (2) [determinism MEDIUM] the freshly-folded
    canon is threaded into `context_for` (it re-reads from disk, so without this the canon was one beat
    behind / inert on a node's first roll); (3) [robustness] `ledger_delta_llm` guards `prior.get("facts")`;
    (4) recursive egress strip; (5) L2 default-off + short timeout. Tests: `test_lucid_canon_integration.py`
    10/10 (per-node fold, branch isolation, cache-no-reroll, headroom-skip, fail-open, recursive strip,
    no-mutation) + `test_lucid_linear.py` 43/43 regression green. Residuals (Low, documented): pre-seal
    canon re-rolls are non-deterministic until sealed (self-stabilizes); L2 `unknown` conflates
    no-measure causes (only matters if L2 is ever promoted from flag to gate).
  - Branch/revert pinned as O(spine) off-lease text passes (the per-node cache makes a revert read the
    sealed node's canon with no re-roll). **Owed: GPU e2e on a non-private dream + a clean commit once the
    parallel WIP in `lucid_linear.py`/`lucid_web.py` settles.**

## Seams (verified against live code, 2026-06-21)
- `lucid_linear.py:990` — `gate_prompt` red-line (safety authority; the ledger/flag are NOT this).
- `lucid_linear.py:1013-1018` — discard / fail-open path (clip None `:1013`; superseded `:1016`).
- `lucid_linear.py:1024-1025` — accept hook + `save_chain` (L0 write + forward-feed seam).
- `lucid_linear.py:1090`/`:1096` — `context_for` labels-only (L0 replaces the `" -> "` join).
- `lucid_facecv.py:42-54` — `faces()` fail-closed-None contract (L2 copies this shell pattern).
- `lucid_engine.ground_frame`/`ground_subject` — existing grounding primitives (tested by
  `test_lucid_ground.py`; note: those 8 tests pin the rating/caption path, NOT the proposed L0/L2
  logic — new tests owed).
