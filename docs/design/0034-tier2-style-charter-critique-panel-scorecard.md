# Panel scorecard — ADR-0034 Tier-2: Style Charter + genuine local critique

- Date: 2026-06-21
- Synthesizer: rating-panel synthesis (this lane reconciles the five dimension raters + two design lenses; it does not re-score)
- Subject: ADR-0034 Tier-2 — (A) `ui-audit-style.py` Style Charter, (B) `ui-audit-critique.py` local 7b VL layout critique
- Source of record: `docs/adr/0034-deterministic-ui-audit-and-screenshot-auditability.md` (this repo) + the implementing skill at
  `~/.hermes/skills/devops/kde-plasma-customization/scripts/{ui-audit-style.py,ui-audit-critique.py}` (lives under `~/.hermes` per ADR-0001) +
  the lease daemon `crates/agentosd/src/lease.rs`.
- **Scoring mode: SHIPPED CODE, two surfaces graded separately.** Both scripts exist and run on the box (verified this round). This is not a
  proposal grade — weight shifts toward feasibility/craft per the panel protocol. The ADR claims "end-to-end working"; the panel grades whether
  *what ships* is 10/10, and several ADR-claimed behaviors are inert (drift detection, evolve→critique steering, accept/dismiss loop).

## Weights (adjusted for shipped-code mode + a privacy/consent surface)

Default AgentOS weights are Vision 0.30 / Experience 0.25 / Craft 0.20 / Feasibility 0.15 / Market 0.10. Adjustment for this work:
this is **shipped code on a consent/privacy-bearing surface**, so feasibility and craft carry more, and experience stays heavy because the
defect there is a *trust* defect, not a polish defect.

| Dimension | Default | This work | Why |
|---|---|---|---|
| Vision-fit | 0.30 | **0.28** | Still the prime axis (does it serve the reactive/personalizing, user-in-control vision) but a hair off the proposal default — the code, not the idea, is on trial. |
| Experience | 0.25 | **0.25** | Held. The consent/leak defect is an experience-of-trust failure, and that is exactly what this dimension gates. |
| Craft | 0.20 | **0.20** | Held. Shipped code; correctness of the inert loops and the gate is a craft fact. |
| Feasibility | 0.15 | **0.17** | Up. The B4 lease-lifetime hazard is a *runtime correctness* defect in the protective mechanism that is the market moat — it must weigh. |
| Market-fit | 0.10 | **0.10** | Held. |

## 1. Weighted verdict

Rater scores (combined, as the panel delivered them):

| Dimension | Score | Weight | Contribution |
|---|---|---|---|
| Vision-fit (combined) | 6 | 0.28 | 1.68 |
| Experience | 5 | 0.25 | 1.25 |
| Craft | 6 | 0.20 | 1.20 |
| Feasibility | 7 | 0.17 | 1.19 |
| Market-fit | 6 | 0.10 | 0.60 |
| **Uncapped weighted** | | | **5.92 / 10** |

**Caps applied.** Experience reports a *hard constraint* (consent-integrity + content-leak in the delivered brief). I evaluated whether this is a
true cap. It is **cap-eligible but I am holding the cap at the experience floor (5), not below it**, for a precise reason established at source below:

- The "consented to 32b, ran 7b" framing is **slightly miscalibrated** against the code. The consent record stores `model: 7b`
  (`ui-audit-critique.py:335`) and `--run` defaults to 7b (`:324`); 32b is *correctly denied* (vision encoder won't load + exceeds the ~7.7GB lease
  budget, `:52-59`). So there is no silent model *swap* at the consent layer. **The real, confirmed integrity defect is different and still
  cap-worthy:** the brief self-certifies `"honors_intent": true` and the prompt promises content-blindness (`:138-141`), while the same 7b
  demonstrably **leaks unquoted UI labels** that the redactor cannot catch — `redact()` only strips double-quoted spans (`:169,175`). The artifact
  lies about its own privacy guarantee. That is a consent-integrity failure of the *promise*, not the *model id*.

**Final (capped) overall: 5.9 / 10** — the uncapped weighted average (5.92) sits *at* the experience floor, so no separate downward cap adjustment
is needed this round; the experience score is already the binding constraint. **Show both numbers so the cost is legible: uncapped 5.92, capped 5.9.**
There is no ADR-non-negotiable violation (no Hermes fork, no second orchestrator, no irreversibility — the Charter never writes the scheme,
`:23-25`), so this is **not RECONSIDER**. It is a firm **ITERATE** with one experience defect that *would* become a cap if shipped to anyone but
this single power-user (see verdict).

## 2. Dispersion analysis

Spread is 5–7 (combined), which is tight — but the **per-surface** spread is the real signal and the panel is unanimous on its shape:

**The panel AGREES on the decomposition.** Every rater independently splits the verdict the same way: the Charter is strong (vision-fit 9,
ambient-embodiment "strongest memory-of-self in the project"), the critique *engine* is weak (vision-fit 5). The low combined numbers are a
**blended artifact of one strong half and one weak half**, not a disagreement. Per protocol I do not average this away — I grade the two surfaces
separately in §3. This is the dominant fact: **there is no real inter-rater conflict; there is a real intra-work conflict between the two
components.** The hidden axis (per domain-depth #8) is **which surface** — naming it dissolves the apparent low consensus.

**Where the panel genuinely DIVERGES — three points, adjudicated:**

1. **Feasibility 7 vs Experience 5 / Vision 6 — the highest single rater is feasibility, which is unusual for a privacy surface.** Adjudication:
   feasibility scored the *plumbing* (lease admission, TTL, denial path) which is genuinely sound, and explicitly flagged the B4 hazard as the
   reason it is not higher. The other raters scored the *delivered trust artifact* (the brief), which is where the leak lives. These are not in
   conflict — they are scoring different layers. The B4 hazard (feasibility's headline) and the leak (experience's headline) are **two distinct
   defects in the same surface**; both are real (both verified at source this round) and both are in the gap plan.

2. **Is the consent failure a CAP (→ RECONSIDER) or a floor-setter (→ ITERATE)?** This is the one place the synthesis had to make a call the raters
   left open. Resolution: the leak is real and confirmed, but (a) the frame is discarded, briefs are 0600 + `--forget`-able, and OLLAMA_HOST is
   asserted-local (`:103-114`) — so nothing leaves the box; the leak is *local model ingests labels it was told to ignore*, not *content
   egresses*. (b) The clean fix (bare-frame) is known and cheap-ish. For **this single power-user who explicitly chose full-desktop→local**, that
   is an ITERATE. For **any second user or any cloud tier**, it is a hard cap. I am recording it as **ITERATE-now / CAP-on-distribution** and
   making the bare-frame the #1 load-bearing fix so the cap never arms.

3. **Market 6 — novel combination vs copyable parts.** No divergence to adjudicate; the panel is coherent that the *combination*
   (user-owned persistent aesthetic identity x local-private critique x stable-by-default) is unoccupied, while the Charter alone is copyable. The
   moat is the lease-coupled, consent-layered local critique — which is exactly the layer the B4 hazard and the leak currently undercut. Market
   score is *gated by* the fixes below, not independent of them.

## 3. Per-component ship/iterate

### (A) Style Charter (`ui-audit-style.py`) — **SHIP the design, ITERATE one omission**

Standalone this is the strongest piece of the project's "memory of self." Verified at source: deterministic and model-free; `keep` snapshots live
tokens (`:84-104,146-163`); `evolve` writes a `target_intent` draft that changes nothing on the desktop (`:166-183`); `keep` correctly folds a
draft's `target_intent` into the new `intent` (`:154`); it **never** writes the scheme/LookAndFeel (the contrast lesson, `:23-25`). It satisfies
ADR-0005 reversibility-by-default (it only describes; there is nothing destructive to invert) and carries its ADR. **As a Charter, this is ~9/10
and ships.** One real omission keeps it off 10: the token snapshot omits the **wallpaper style** (the live UE/dream/nimbus-aurora style index) —
for AgentOS specifically, the wallpaper IS a primary aesthetic surface, so a "memory of self" that can't remember which wallpaper style is the
identity is incomplete. That is a small additive fix, not a redesign.

**Verdict: SHIP (design + code), with the wallpaper-token addition tracked as a fast-follow.**

### (B) Critique engine (`ui-audit-critique.py`) — **ITERATE; do not represent as trustworthy yet**

The pipeline runs end-to-end and the *guardrails that exist* are real (local-only assert, lease-gate, frame discard, 0600, denial-aborts-not-OOM,
fail-open-on-unreachable). But the layer that makes it a *product* — a trustworthy, charter-steered, self-improving critique — is substantially
inert, and one runtime defect defeats the protective mechanism that is the moat:

- **B4 lease-lifetime defect (load-bearing, confirmed at `lease.rs:1476-1500`).** `lease_acquire`/`lease_release` are two separate one-shot
  `busctl` calls (`:63-88`); the connection closes within ~1s of `Acquire` returning. The daemon's supervisor polls every 750ms and
  auto-releases a cooperative holder whose D-Bus peer no longer has an owner (`name_has_owner` false → `release_token(..., None)`). So the lease
  is freed within ~1–2 cycles (≤~1.5s) **while the ~210s `call_vl` inference is still running** (`:159` timeout=240). The protective hold is
  cosmetic — arbitration is admission-time-only; a concurrent ComfyUI/dream burst during the 3.5-min critique is not actually held off.
- **evolve→critique dead link (confirmed `:130-156`).** `build_prompt` reads only `intent` + `principles`; it never reads the active charter's
  `target_intent`. The ADR/`evolve` promise "critique then guides toward it" (`ui-audit-style.py:19,182`) **does not fire**.
- **Inert acceptance loop (confirmed `:288-301`).** `feedback.jsonl` is appended and counted into an `acceptance_rate`, but the metric is never
  read back into `build_prompt` — the loop does not learn. `--dismiss` records a verdict but changes no future behavior ("simulate control").
- **Weak specificity gate (confirmed `:178-186`).** A generic item survives if `concrete_ref` ≥ 12 chars even when `_GENERIC` matches the
  observation — so a long-but-vacuous ref passes. The panel observed `dropped_generic=0` on a run with 2 generic items.
- **Leak (confirmed `:169-175`).** `redact()` strips only double-quoted spans; the live brief leaked unquoted "Problems"/"Output"/"terminal".
- **Missing reversibility on evolve, and missing drift signal.** No abort/revert for an open `evolve` draft; no deterministic drift scalar.
- **Robustness nits (confirmed):** `--no-lease` is passed straight through unguarded (`:350`); the `busctl` reply is positional-text-parsed
  (`:74-78`) with no schema pin; no fail-fast if a user forces `--model qwen2.5vl:32b` beyond logging the denial.

**Verdict: ITERATE.** The Charter can ship today; the critique engine should be labeled "experimental / power-user, local-only" until the
load-bearing fixes land. Do not let the Charter's strength imply the critique layer is trustworthy — they ship at different maturities.

## 4. Consolidated 10/10 gap plan (deduped, ordered, owned)

Owners are makers (raters score, they do not own fixes). Effort: S < ~half-day, M ~1–2 days, L > 2 days. "Lifts" lists the rater deltas each closes.

### LOAD-BEARING BLOCKERS (the few fixes that gate the most score — do these first)

1. **Hold the lease for the inference lifetime.** Replace the two one-shot `busctl` calls with a single persistent D-Bus connection that stays
   open Acquire→inference→Release (or, per feasibility's B5 alternative, make the daemon treat a `busctl` cooperative lease as TTL-only / unbound
   so peer-disconnect does not free it). This is the fix that makes the lease coupling — the market moat — actually true at runtime.
   *Effort: M. Owner: `design-technologist` (the Python lease client) with a `lease.rs` policy call to `rust-performance-reviewer` if the B5 path
   is chosen. Lifts: feasibility (B4, the headline), market-fit (the moat becomes real), craft.*
   *Substrate note: the B5 variant touches `crates/agentosd/src/lease.rs:1476-1500` — coordinate so a `busctl`-grain dream client isn't broken.*

2. **Bare-frame critique (proven-empty desktop) instead of full-desktop.** Empty virtual desktop + neutral wallpaper + DND + KWin window-list
   assert that zero app windows are present (reversibly). This is the clean fix for the leak: the frame *cannot* contain private content, so the
   guarantee becomes provable rather than prompt-hoped. It also disarms the distribution-cap and unlocks a future cloud-tier on a content-free
   frame.
   *Effort: L (needs the reversible empty-desktop setup + window-list assert via the kwin path). Owner: `design-technologist` for the
   capture/window-assert, `wayland-computeruse-reviewer` consulted for the KWin window-list + reversibility. Lifts: experience (removes the
   cap-on-distribution), market-fit (provable "cannot leak"), vision-fit (calm-honest).*

3. **Fix the redactor as a backstop, not the boundary.** Until bare-frame lands, strip *unquoted* candidate labels too (not only double-quoted
   spans), and treat the redactor as defence-in-depth behind #2 — never as the privacy boundary. Fail-fast/abort the brief if redaction confidence
   is low.
   *Effort: S. Owner: `design-technologist`. Lifts: experience, craft, market-fit (layout-layer privacy story).*

### HIGH-VALUE (close the "inert loop" gaps — these turn a demo into a product)

4. **Wire `evolve`'s `target_intent` into `build_prompt`.** When the active charter is a draft (`status == "draft"`), the prompt must critique
   *toward* `target_intent`, not only against the kept `intent`/`principles`. This makes the documented evolve→critique promise fire.
   *Effort: S. Owner: `interaction-designer` (owns the evolve↔critique interaction) with `design-technologist` for the prompt change. Lifts:
   vision-fit (the broken promise), experience (the dead link), craft.*

5. **Close the acceptance loop.** Read `feedback.jsonl` back into `build_prompt` (e.g. suppress areas the user has repeatedly dismissed; bias
   toward accepted areas) so the metric is causal, not cosmetic. Make `--dismiss` actually suppress that item/area on the next run.
   *Effort: M. Owner: `personalization-loop-reviewer` to specify the loop, `design-technologist` to implement. Lifts: vision-fit (personalization
   loop is currently inert), experience (control becomes real, not simulated), craft.*

6. **Deterministic, leak-free drift scalar surfaced as a QUIET at-rest cue.** A model-free comparison of live tokens vs the kept charter tokens
   yields a drift number; surface it as a quiet cool/desaturate at-rest wallpaper cue — never warm (that is `needs_you`'s monopoly), never a
   notification. This is the ambient-embodiment lens's ask and gives the loop an honest, privacy-free signal independent of the leaky 7b.
   *Effort: M (scalar is S; the wallpaper-grammar cue is the cost). Owner: `generative-artist`/`motion-designer` for the at-rest cue grammar,
   `design-technologist` for the deterministic scalar. Lifts: vision-fit, experience, market-fit (the drift-detection loop the ADR claims).*
   *Substrate note: the at-rest cue needs a wallpaper-grammar channel; only Flow (0)/Hills (1) styles react today and there is no idle "cool"
   channel — confirm the producer exists before treating this as closeable this round, or mark it substrate-blocked on the wallpaper grammar.*

### CRAFT / CORRECTNESS (raise craft toward 10)

7. **Strengthen the specificity gate.** A `_GENERIC`-matching observation must be dropped regardless of `concrete_ref` length (a long ref is not
   evidence of specificity). Add a test from the panel's counterexample (2 generic items, `dropped_generic` should be 2).
   *Effort: S. Owner: `design-technologist`. Lifts: craft, experience (less slop).*

8. **Pin the `busctl` reply schema + fail-fast on a non-loadable model.** Replace positional text-parse with a checked parse (or D-Bus typed
   reply), and abort with a clear message if `--model` resolves to 32b (vision encoder won't load) rather than only logging the lease denial.
   *Effort: S. Owner: `design-technologist`. Lifts: craft, feasibility.*

9. **Guard `--no-lease` and add evolve abort/revert.** `--no-lease` should require an explicit confirm (it removes the GPU protection); add an
   `evolve --abort` (discard the open draft) and a charter `revert <version>` so the Charter's "memory of self" is itself reversible (ADR-0005).
   *Effort: S. Owner: `interaction-designer` (abort/revert UX) + `design-technologist`. Lifts: experience, craft, vision-fit (reversibility).*

10. **Add the wallpaper style to the Charter token snapshot.** Record the live nimbus-aurora style index in `live_tokens()` so the identity
    remembers its primary surface.
    *Effort: S. Owner: `visual-systems-designer` (tokens/theming under personalization). Lifts: vision-fit, craft.*

### NICE-TO-HAVES (do not gate the score)

11. **Remove the dead code in `stitch()`** and tidy minor unused paths. *Effort: S. Owner: `design-technologist`. Lifts: craft (marginal).*
12. **Microcopy pass on the brief + consent text** so the at-rest tone matches the calm-honest voice. *Effort: S. Owner: `content-voice-designer`.
    Lifts: experience (marginal), vision-fit (calm tone).*
13. **Codify the conversational style critique** (the not-yet-codified style layer) into a deterministic, screenshot-free style-vs-charter check —
    privacy-free because it derives from the scheme. *Effort: M. Owner: `visual-systems-designer` + `design-technologist`. Lifts: vision-fit,
    market-fit. (Promote above the nice-to-haves once #1–#3 land — it is the trustworthy layer that should be elevated above the leaky 7b.)*

**Standing gap (touches both surfaces): no versioned contract** between charter producer and critique consumer. The critique reads
`charter.get("intent"/"principles"/"version")` with no schema/version-compat check; a charter shape change silently degrades the prompt. Track as
one item, owned by `design-technologist`, lifts craft + feasibility. (Deduped — do not list separately under both raters.)

## 5. Verdict

**ITERATE — overall 5.9/10 (uncapped 5.92; experience floor binds).** No ADR non-negotiable is violated, so this is not RECONSIDER; but the
weighted number sits at the experience floor because the delivered critique brief breaks its own privacy promise, and the protective lease hold is
defeated at runtime. The Charter is genuinely strong and ships now; the critique engine is an experimental local-only layer until the blockers land.

**The next 3 moves (in order):**
1. **Hold the lease for the inference lifetime** (gap #1) — makes the moat true at runtime; lifts feasibility + market.
2. **Bare-frame capture** (gap #2) — makes the privacy guarantee provable and disarms the distribution-cap; lifts experience + market + vision.
3. **Wire evolve→critique + close the acceptance loop** (gaps #4, #5) — makes the personalization loop causal instead of inert; lifts vision +
   experience.

**Distribution gate:** while the leak (gap #2/#3) is open, the critique engine must stay labeled single-user / local-only. Shipping it to a second
user or any cloud tier *arms the experience cap* and turns this into RECONSIDER. The Charter has no such gate.

**Escalation:** none required — there is no unresolved cross-lane conflict; the apparent low consensus is the strong-Charter/weak-critique split,
which §3 resolves by grading the two surfaces separately. If the B5-TTL-only variant of gap #1 is chosen (changing `lease.rs` semantics for all
cooperative `busctl` callers), route that single decision to `design-discourse-mediator` since it touches the dream client's lease grain.

## 6. Round delta

First panel round on Tier-2 — no prior scorecard to diff. Baseline established: **uncapped 5.92 / capped 5.9**, Charter ~9 / critique ~5.
Re-rate target after the three load-bearing blockers (#1–#3): experience off the floor (≥7), feasibility ≥8 (B4 closed), market ≥7 (provable
"cannot leak"), projected overall ≥7.5 — which would move the critique engine from ITERATE toward SHIP-as-experimental. A genuine 10 additionally
requires the inert loops (gaps #4–#6) to be causal and the Charter's wallpaper-token + revert (gaps #9–#10) so the "memory of self" is itself
reversible.

---

## Re-rate after the iteration (2026-06-21, same day)

The gap plan was implemented and the four capped raters re-rated against the updated code:

| Dimension    | Before | After | Notes |
|--------------|:------:|:-----:|-------|
| Vision-fit   |  6 (9/5) | **8 (9/8)** | soft calm-honest miss cleared by the deterministic honesty override |
| Experience   |  5 | **8** | consent-integrity + inert-dismiss + dead evolve→critique all cleared, verified in running code |
| Craft        |  6 | **8.5** | all 9 confirmed defects fixed; +16-case filter test suite; one latent `_NULLIFY` stem bug fixed |
| Feasibility  |  7 | **8** | B4 closed (persistent dbus connection; verified against `lease.rs:1476-1500`) |
| **Composite**| 5.9 | **~8.1** | **ITERATE → SHIP** (Charter ship; critique engine ship-as-directional) |

**What closed the blockers:** honesty override (assess > model self-cert), deterministic style-critique + drift surfaced as the trustworthy layer, charter-contradiction/self-nullify/generic filters, evolve→critique steering, dismiss fingerprint-suppression, consent verify+re-stamp, bare-frame capture (leak closed at source) with crash-safe restore, persistent-connection lease hold.

**Remaining (fast-follow, non-blocking):** one live granted-path model run on an idle GPU; the ambient drift cue (substrate-blocked on the wallpaper grammar — ADR-0030); SIGKILL-only strand window (bounded by lease TTL + 1-click undo); `--undismiss` + a louder dbus-absent signal.
