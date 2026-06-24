# 0002 — Dreaming (ADR-0008) Panel Scorecard

- Date: 2026-06-16
- Synthesizer: rating-panel synthesis (this agent)
- Scope: **status review of the uncommitted "dreaming via local video" delta**
  (ADR-0008 + `dreaming/` ComfyUI scaffold + `docs/research/0001`) sitting on the
  **committed `agentosd` substrate** (`crates/agentosd/src/main.rs` read-only NVML/Ollama
  monitor + `feed.rs` P1/P2 `agent.json` producer).
- Mode: **MIXED.** `feed.rs` / `main.rs` are *shipped code*; the entire video path
  (`dreaming/`) is a **design proposal with throwaway scaffold**, and ADR-0008's headline
  guarantee — agentosd owning ComfyUI as an evictable lease — is **zero lines of code**.
  Per synthesis rule #2, the verdict weights the *unbuilt* path as a proposal, not as merged
  code, and says so wherever a rater scored it as if the code existed.

---

## 1. Overall — weighted, caps applied (math shown)

### Weighting rationale (adjusted from defaults, with reason)
This is a **resource + safety substrate** (ADR-0001), and the delta under review is precisely
the high-VRAM, autonomous-content, desktop-mutating surface the substrate exists to *contain*.
Per the default note ("adjust per the work, with a reason"), I shift weight off market-fit and
craft and onto **feasibility and vision-fit**, and I treat the experience a11y/agency breach as
a **hard cap** (synthesis rule #1):

| Dimension | Default | This review | Why |
|---|---|---|---|
| Vision-fit | 0.30 | **0.30** | Holds — does this serve "substrate, user keeps control" (ADR-0001/0005)? |
| Feasibility | 0.15 | **0.30** | Raised — the load-bearing lever (`/free`) **empirically failed a live test**; for a *safety* substrate, "does the safety mechanism work" dominates. |
| Experience | 0.25 | **0.20** | Slightly lowered as a *weight*, but it carries a **hard cap** (a11y/agency breach), so its real influence is larger than its weight. |
| Craft | 0.20 | **0.12** | Lowered — polish on a scaffold whose premise is unproven is not where the score lives. |
| Market-fit | 0.10 | **0.08** | Lowered — "rate the loop, discount the frames"; the wedge is the loop, which is still scaffold. |

### Raw weighted (uncapped)
| Dimension | Score | Weight | Contribution |
|---|---|---|---|
| Vision-fit | 7.0 | 0.30 | 2.10 |
| Feasibility | 4.0 | 0.30 | 1.20 |
| Experience | 4.0 | 0.20 | 0.80 |
| Craft | 6.5 | 0.12 | 0.78 |
| Market-fit | 6.0 | 0.08 | 0.48 |
| **Uncapped weighted** | | 1.00 | **5.36 / 10** |

### Caps (evaluated BEFORE the average is final — synthesis rule #1)
Two cap-eligible conditions are **live**:

1. **Experience a11y + agency cap → 4.** `krunner_video_runner.py:64-65` auto-`xdg-open`s an
   NSFW-capable clip from a *global launcher* with no preview, consent, or opt-out, and dumps a
   raw traceback into a notification (`:65`, `else ... "$OUT"`). The runner is
   `EnabledByDefault=true` (`agentos-video.desktop:12`). This is an agency + accessibility floor
   breach. **Cap = 4.**
2. **Fail-open / resource-safety cap → 4 (feasibility).** The mechanism that makes "dreaming is
   evictable, inference always wins" *true* (ADR-0008 §4, `POST /free`) **failed a live test at
   idle**: VRAM 21540 → 21571 MiB (no drop), ComfyUI still holding 17110 MiB. An uncoordinated
   detached generation (`krunner_video_runner.py:99` → `:56` → `:67`) can OOM the desktop *under
   inference* — the exact harm ADR-0001 exists to prevent. A safety claim whose enforcement
   mechanism is empirically non-functional caps the dimension. **Cap = 4.**

A capped dimension caps the overall (synthesis rule, restated in the panel charter). The binding
cap here is **4** (both live caps sit at 4).

### Final
- **Uncapped weighted: 5.36 / 10**
- **Caps live at 4 (experience a11y/agency; feasibility fail-open).**
- **FINAL OVERALL: 4 / 10 (capped).**

The 1.36-point gap between 5.36 and 4 is the *cost of the violations made legible* (rule #1):
until the auto-open/consent breach and the non-functional eviction lever are removed, the
weighted average is advisory only. A cap means the verdict is governed by **removing the
violation**, not by the headline number.

---

## 2. Per-dimension — score + the one thing each needs

| Dimension | Score | The one thing |
|---|---|---|
| **Vision-fit** | 7.0 | Move the eviction lever *into agentosd* (a real lifecycle owner), not narrated-in-ADR / fire-and-forget in `comfy_client.py:402`. The vision ("agentosd owns the lease") is asserted but not embodied. |
| **Craft** | 6.5 | Test `ui_to_api` — the most logic-dense code in the delta (`comfy_client.py:207-276`, plus `flatten_subgraphs:108`) has **zero** tests, while the simple `feed.rs` mapping has 11. |
| **Experience** | 4.0 (capped) | Remove auto-`xdg-open`; add preview + explicit consent + opt-out + progress/cancel before any clip touches the screen (`krunner_video_runner.py:56-67`). |
| **Feasibility** | 4.0 (capped) | Make eviction *real*: agentosd must OWN the ComfyUI PID and SIGKILL it (admission-control, predict-before-load), because `/free` provably does not reclaim VRAM mid-run. |
| **Market-fit** | 6.0 | Land the closed loop (fleet-state → local video → reversible wallpaper) + the working VRAM lease — both are the defensible wedge and both are still scaffold; the frame-generation layer is commoditized (DeskScapes 2026, ComfyUI+Wan+Civitai). |

---

## 3. Dispersion analysis — reconcile Experience 4 & Feasibility 4 vs Vision 7

**Variance is high (4 → 7, spread 3).** Per synthesis rule #8, high variance hides an unstated
mode assumption. Three axes are in play here, and the split is *not* noise to be averaged:

- **Axis A — proposal-vs-code (the dominant one).** Vision-fit scored the *idea* ("agentosd owns
  an evictable video lease; dreaming degrades to the proven shader; one backend, two surfaces") —
  which is genuinely on-vision, hence 7. Feasibility and experience scored the *artifact that
  exists* — a scaffold whose eviction lever fails live and whose runner auto-opens NSFW content —
  hence 4. **Adjudication: both are correct about different objects.** The reconciliation is *not*
  the 5.36 midpoint; it is "the proposal is sound (7), the implementation is unsafe-and-unproven
  (4), and because this is a safety substrate, the unsafe artifact governs the ship decision."

- **Axis B — best-effort vs guaranteed.** ADR-0008 §4 says "dreaming is best-effort and
  offline/cached; live inference outranks dreaming." Vision read that as a *design intent* (fine).
  Feasibility read it as an *enforcement claim* and tested it — and the enforcement mechanism
  (`/free`) does not enforce. A "best-effort availability" score and an "enforcement works" score
  legitimately diverge; the ADR makes an enforcement promise (§4: "the coordinator can evict")
  that the code cannot keep, so feasibility's lower read is the binding one for a substrate.

- **Axis C — which surface.** Surface B's KRunner D-Bus *matching/dispatch* path is sound and
  low-risk (all four reviewers and the feasibility rater agree). The *consume* path (detached gen,
  auto-open, no lease) is where the caps live. Averaging across the two surfaces would hide that
  the trigger is fine and the payload is dangerous. **Don't split — name it:** ship-track the
  D-Bus seam, hold the payload.

**Hidden-assumption verdict:** the 7-vs-4 split is the proposal/code seam (rule #2), not a
disagreement about quality. I do **not** average it to 5.36 as the headline — the cap stands and
the verdict is governed by Axis-A's unsafe artifact.

---

## 4. Consolidated 10/10 gap plan — deduped, ordered by leverage

Each item: **what** · **file:line / ADR** · **who owns it** (real maker, never a rater) · **which
dimension(s)/persona(s) it lifts**. Items marked **[SUBSTRATE-BLOCKED]** cannot be closed this
round; their blocker is named (rule #4). Deduped where craft+feasibility / multiple reviewers
raised the same anchor (rule: one item, one owner, list all deltas it closes — e.g. the contract
and the `/free` lever each appeared under several personas and are merged here).

### Tier 0 — CAP REMOVERS (the verdict cannot rise above 4 until these land)

1. **Kill the auto-open + add consent/preview/opt-out; make ambient SFW-only; fail-closed on the
   red line.**
   `krunner_video_runner.py:56-67` (auto-`xdg-open`, raw-traceback notify),
   `agentos-video.desktop:12` (`EnabledByDefault=true`), ADR-0008 §6 (red line has zero
   enforcement).
   Owner: **interaction-designer** (preview/consent/cancel flow) + **design-technologist**
   (fail-closed guard, opt-in default, SFW-only-ambient gate) + **content-voice-designer**
   (error microcopy replacing the raw traceback dump).
   Lifts: **Experience 4→ (removes the a11y/agency cap)**; closes responsible-ai + ux-reviewer +
   ui-accessibility-reviewer deltas.

2. **Make eviction real: agentosd OWNS the ComfyUI PID with admission-control (predict-before-load)
   + SIGKILL release; demote `/free` to a best-effort hint, not the lever.**
   ADR-0008 §4; `comfy_client.py:402` (fire-and-forget `free_vram`); `main.rs:16` ("No eviction,
   no `ollama stop`, no nimbus-flux kill/relaunch yet"); empirical: `/free` left VRAM
   21540→21571 at idle.
   Owner: **design-technologist** (process-supervision + admission-control design) with
   **resource-safety-reviewer** and **rust-performance-reviewer** consulted on the blocking-Rust /
   async-runtime tradeoff.
   Lifts: **Feasibility 4→ (removes the fail-open cap)**; closes resource-safety + security
   (GPU-DoS) deltas. **[SUBSTRATE-BLOCKED]** for the *implemented* form: the VRAM coordinator is
   zero lines of Rust (`main.rs:16`); this round can land the **design** (admission-control +
   PID-ownership lifecycle), not the running coordinator. Ship the design; do not imply the code
   exists.

### Tier 1 — SAFETY/CORRECTNESS (high leverage, mostly closeable as design now)

3. **Replace `bash -lc` with an argv exec; bound generation; authenticate the D-Bus `Run()`.**
   `krunner_video_runner.py:59-67` (shell string), `:99` (`Run` → unbounded 24GB gen);
   `comfy_client.py:359-385` (ComfyUI-returned `filename`/`subfolder` path-injection then
   `xdg-open`).
   Owner: **design-technologist**, security-reviewer consulted.
   Lifts: **Feasibility, Craft**; closes security (GPU-DoS, path-injection) + determinism deltas.

4. **Bind the produced artifact to `prompt_id`; validate the artifact; remove the mtime race.**
   `comfy_client.py:376-399` (`_newest_video` mtime race across two surfaces sharing one backend;
   silent wrong-video return), `:359-370` (`output_files` trusts returned paths).
   Owner: **design-technologist**, determinism-safety-reviewer consulted.
   Lifts: **Feasibility, Craft**; closes determinism + ai-generation deltas.

5. **Put the wallpaper-swap (active-dream selection) inside the ADR-0005 tx envelope; keep idle
   byte-identical.**
   ADR-0008 §57-64 ("instant shader fallback" is asserted as reversibility but is **not** a
   registered inverse). The *generation* is correctly outside the tx (good); the *desktop
   mutation* of selecting/playing a dream is not.
   Owner: **interaction-designer** (diff/revert interaction) + **design-technologist** (tx-inverse
   registration), reversibility-tx-reviewer consulted.
   Lifts: **Vision-fit, Experience**; closes reversibility + ambient deltas. **[SUBSTRATE-BLOCKED]**
   for the *implemented* tx: ADR-0005's apply/rollback tx is zero lines of Rust. Land the design;
   the running inverse is blocked on the unbuilt tx.

### Tier 2 — HONESTY OF THE GRAMMAR / EMBODIMENT (vision integrity)

6. **Define an honest `{state,busy,warm,snag}` → clip mapping that does NOT demote the proven
   shader grammar to "fallback," and keep idle a no-op.**
   ADR-0008 §32-33 (shader → "reduced-motion / fallback renderer" inverts the embodiment vision);
   `feed.rs:78-98` emits continuous floats that "cannot survive discretization into a few loops";
   `feed.rs:185-193` — note `derive_feed` **never emits state 3 (`acting`)**, so any clip mapping
   that keys on `acting` is dead on arrival.
   Owner: **ambient-embodiment-reviewer** consult → **generative-artist** / **motion-designer**
   own the grammar; **personalization-loop-reviewer** consulted on the loop.
   Lifts: **Vision-fit, Experience, Market-fit (the loop is the wedge)**. The `acting`-state
   dependency is **[SUBSTRATE-BLOCKED]**: `derive_feed` never produces state 3 and the
   computer-use backend it would depend on is unbuilt.

### Tier 3 — CRAFT (lower leverage; do not let polish masquerade as a 10)

7. **Test `ui_to_api` + `flatten_subgraphs` (the logic-dense converter).**
   `comfy_client.py:108-276` — zero tests on the most complex code in the delta.
   Owner: **design-technologist**. Lifts: **Craft**; closes the rater-craft headline delta.

8. **Make paths configurable; replace the `sed`-scrape result parse with structured output.**
   `comfy_client.py:34` + `krunner_video_runner.py:41` (hardcoded home path);
   `krunner_video_runner.py:63` (`sed -n` scrape of stdout — fragile, and the same seam that lets
   the wrong/poisoned path through).
   Owner: **design-technologist**. Lifts: **Craft, Feasibility**.

9. **Ship a scripted inverse for the install path (apply/restore pair, à la
   `crates/agentosd/dist/{apply,restore}.sh`).**
   `dreaming/README.md:46-55` (5-step manual install, no uninstaller); `agentos-video.desktop`.
   Owner: **design-technologist** (script) + **content-voice-designer** (README humanizing).
   Lifts: **Vision-fit (reversibility, rule #9), Experience (inhumane install)**.

10. **Add a versioned, schema'd contract across the producer/consumer boundary (standing gap).**
    `feed.rs:54-60` + ADR-0008's new `agent.json`→clip consumer — the contract
    `{"state":N,"busy":f,"warm":f,"snag":f}` is pinned only by a serde round-trip test
    (`feed.rs:343-349`), no JSON Schema, no versioning. ADR-0008 adds a *second* consumer of this
    contract, raising drift risk.
    Owner: **visual-systems-designer** (token/contract under personalization) +
    **design-technologist**. Lifts: **Craft, Feasibility** (this is the deduped single item for
    the contract concern that craft + feasibility both raised — one owner, two deltas, per the
    "don't double-count the contract" pitfall).

### Tier 4 — CORRECTIONS TO THE PANEL (ground-truth fixes, no owner action)

- **rater-craft's "committed `__pycache__/*.pyc` + no `.gitignore`" is WRONG on both counts.**
  A root `.gitignore` exists and explicitly ignores `__pycache__/` and `*.pyc`
  (`.gitignore:9-12`). The
  `dreaming/__pycache__/comfy_client.cpython-314.pyc` on disk is therefore **not tracked** — it's
  a stray local build artifact git already ignores. Drop this from the craft delta; it does not
  lower the score. (Craft 6.5 otherwise stands.)

---

## 5. Verdict — HOLD

**HOLD** (this panel's RECONSIDER-equivalent: a capped dimension demands a rethink, not an
iteration on polish). Two live caps at 4 — the experience a11y/agency breach and the empirically
**failed** eviction lever — mean the overall cannot exceed 4 regardless of the 5.36 weighted
average. For a *safety substrate*, shipping a path whose safety mechanism provably does not work,
which can OOM the desktop under inference, is the one thing ADR-0001 forbids.

This is **HOLD, not ITERATE**, specifically because the failure is in the *premise* (the lease
lever doesn't reclaim VRAM; the substrate that would own it is unbuilt — `main.rs:16`), not in the
finish. Per the pitfall ("a cap means RECONSIDER until the violation is gone — the weighted average
is irrelevant while a cap is live"), the 5.36 is advisory only.

### Gating conditions to leave HOLD → ITERATE
1. **Cap-remover #1 lands**: no auto-open; opt-in (`EnabledByDefault=false`); preview + consent +
   cancel; SFW-only ambient; fail-closed red-line guard. (Removes the experience cap.)
2. **Cap-remover #2 lands as a sound DESIGN**: agentosd owns the ComfyUI PID with
   admission-control (predict-before-load) and SIGKILL release; `/free` demoted to a hint.
   (Removes the feasibility cap *for the proposal*; the *running* coordinator stays
   [SUBSTRATE-BLOCKED] on `main.rs:16` and ADR-0005.)
3. **Tier-1 safety items (#3, #4) at least designed**: argv exec, bounded/authenticated `Run()`,
   `prompt_id`-bound artifact (closes GPU-DoS, path-injection, wrong-video).

### Gating conditions to ITERATE → SHIP (the design)
- All Tier-0 + Tier-1 closed as accepted designs; Tier-2 grammar honest (shader not demoted; idle
  byte-identical; no `acting`-state dependency); reversibility item #5 + #9 land an apply/restore
  pair and an ADR-0005-shaped tx inverse design. SHIP here means **ship the design**, never "the
  coordinator exists."

### Next 3 moves
1. **interaction-designer + design-technologist**: cap-remover #1 (consent/preview/opt-out/
   fail-closed) — this is the single highest-leverage change in the whole delta.
2. **design-technologist (resource-safety-reviewer consult)**: cap-remover #2 design
   (PID-ownership + admission-control), and re-run the `/free` test to confirm SIGKILL reclaims.
3. **design-discourse-mediator**: adjudicate the Tier-2 conflict — demoting the *proven* shader
   grammar to "fallback" (ADR-0008 §32-33) inverts the embodiment vision and pits ambient-embodiment
   against the video pivot. This is an unresolved cross-lane conflict; escalate.

---

## 6. ADR-0008 Accepted → Proposed recommendation

**Recommend: downgrade ADR-0008 from `Accepted` to `Proposed`.** Three independent grounds, any one
sufficient:

1. **A core decision is empirically false as written.** §4 states agentosd evicts the video
   leaseholder via `POST /free`; the live test shows `/free` does not reclaim VRAM. An ADR whose
   load-bearing mechanism is disproven cannot stand as Accepted — it must be re-proposed with the
   PID-ownership/admission-control mechanism that actually works.
2. **The stated red line has zero enforcement** (§6) while the autonomous surface that could
   violate it (ambient Surface A + `EnabledByDefault=true` runner) is the *default*. Responsible-ai
   correctly asks for Accepted→Proposed until a fail-closed guard + opt-in + SFW-only-ambient exist.
3. **The reversibility claim is overstated** (§57-64): "instant shader fallback" is presented as
   ADR-0005-grade reversibility but registers no inverse, and the proposal demotes the *proven*
   shader grammar — a behavior change that should be re-proposed, not silently Accepted (per CLAUDE.md:
   "Changing behavior → add or supersede an ADR; do not silently drift").

**Keep the pivot's intent.** The *direction* (3D north star, video as the pragmatic-now medium, one
ComfyUI backend / two surfaces, dreaming-as-cached-artifact outside the tx) is on-vision and should
survive re-proposal. Re-issue ADR-0008 as `Proposed` with: (a) the real eviction mechanism, (b) the
fail-closed/opt-in/SFW-ambient posture, (c) an honest grammar that does not demote the shader, and
(d) the desktop-swap inside the ADR-0005 tx envelope. Note the dependency explicitly: ADR-0008's
guarantees are **blocked on the unbuilt VRAM coordinator and apply/rollback tx** — until those exist
in Rust (`main.rs:16`), ADR-0008 is a proposal on top of a proposal, and its status should say so.

---

## Round delta
First round for this delta — no prior scorecard to diff against. Subsequent re-rates should show the
per-dimension delta and, specifically, whether each Tier-0 cap-remover has flipped its cap off
(Experience 4→ and Feasibility 4→ are the two numbers that unlock the overall).
