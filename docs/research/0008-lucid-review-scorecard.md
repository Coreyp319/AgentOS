# Review 0008 — Lucid (ADR-0014) design-council scorecard

- Status: Review complete (2026-06-16). **Verdict: HOLD as a ship design / ITERATE as a spike.**
  No this-pass code fixes applied beyond committing the in-flight engine fixes; this is a
  design review of a `[SUBSTRATE-BLOCKED]` proposal, not a merge gate.
- Subject: [ADR-0014](../adr/0014-lucid-interactive-branching-dream-loop.md) + the throwaway
  spike `apps/dreaming/lucid/{lucid_engine.py,lucid_panel.py,README.md}` (reuses
  `apps/dreaming/comfy_client.py` + the Wan2.2 Remix i2v workflow).
- Panel (8 dimensions): determinism-safety · ai-generation · responsible-ai/privacy ·
  security · resource-safety · reversibility-tx · ai-product/vision-fit · interaction.
- Relates to: ADR-0001 (substrate not orchestrator), 0003/0004 (yield), 0005 (apply/rollback tx),
  0006/0010/0013 (VRAM coordinator), 0009 (Surface B parent — never shipped), 0012 (keyhole).
- Lineage: extends the dreaming/Surface-B council [0002](0002-dreaming-panel-scorecard.md) +
  [0003](0003-dreaming-design-synthesis.md) (which put the video path on HOLD, 4/10).

## Verdict

**The core bet is real and is genuinely this substrate's** — last-frame→first-frame continuity
plus LLM-steered choice is a true upgrade over the ADR-0009 one-shot "slot machine," and "the loop
**is** the VRAM-coordinator dance" is the framing that makes dreaming substrate-differentiated
rather than a DeskScapes clone. The design is honest and ADR-disciplined (names its own owed
reviewers, marks the spike throwaway). **But as written it ships three blocker-class
non-negotiable violations**, and the spike proves the commoditized half (ComfyUI+Ollama chaining)
while skipping the differentiated, risky half (the coordinator turn-taking). Weighted score is
**capped by the safety dimensions**: a non-negotiable violation caps the aggregate.

**Overall: 4/10 (gated).** Not shippable beyond a spike until the blockers clear. The path forward
is not "polish this" — it is **ai-product's reframe**: build the *smallest* lucid (a linear chain,
driven through the real coordinator lease), prove the dance, and gate everything else behind that.

| Dimension | Score | Headline |
|---|---:|---|
| responsible-ai / privacy | **3** | red-line is LLM-only; **real-person seed → i2v likeness unguarded**; 3-sink deletion unspecified |
| resource-safety | **4** | `keep_alive:0` ≠ a gate → beat-gen/video **co-residency OOM**; spike runs ~21.8 GB i2v with no lease |
| reversibility-tx | **5** | `tree.json` torn-write + concurrent-step lost-node; wallpaper-tx contract unspecified |
| determinism-safety | 6 | "validated structured object" is prose; `_sanitize` is length-caps-only; choice-index TOCTOU |
| ai-generation | 6 | free-text path bypasses all hygiene; 300 s blocking beat-gen; no beat-contract eval |
| interaction | 6 | branch-rail/designed-wait claimed but unspiked; TOCTOU = "system acted, blamed you" |
| ai-product / vision-fit | 6 | **ITERATE** — branching is gold-plate; cut to linear MVP; reframe spike as coordinator harness |
| security | 7 | strong fundamentals (no shell-injection, loopback, escaped output); CSRF + path-traversal to fix |

## Blockers (must clear before this is anything but a throwaway spike)

1. **B1 — Co-residency OOM (resource-safety, determinism).** `keep_alive:0` is a fire-and-forget
   async unload, not a confirmed-freed gate (the `/free` mistake from ADR-0009 §1, repeated). The
   spike calls the ~21.8 GB i2v step the instant beat-gen returns; on a 24 GB box the 9.6 GB beat
   model + Wan weights can be co-resident → **desktop OOM/wedge** — the cardinal sin (safety layer
   causes the harm). The spike's docstring even claims it "Honors ADR-0009 §3" while it does not.
   *Fix:* deterministic predict-before-load after a **confirmed** unload (poll `/api/ps`/NVML until
   the beat model is gone, with timeout), then admit via the coordinator `Spawn` (ADR-0010/0013);
   move beat-gen to a CPU/≤2 GB model so it never contends. `keep_alive:0`/`/free` are hints only.

2. **B2 — Real-person likeness via i2v is completely unguarded (responsible-ai, security).** Lucid
   is image-to-video from a user-supplied seed still; the red-line ("no minors, no non-consensual
   real-person likeness") guards only **text**. A photo of a real person → animated person, with
   the adult-tone opt-in reachable in-product. This is the deepfake/NCII mechanism, unaddressed by
   design. *Fix:* the video-side guard must inspect the **image** (face/person detection on seed +
   each anchor frame), block real faces by default, hard-refuse real-face + adult-tone (not
   operator-waivable for third parties).

3. **B3 — Red-line is the LLM grading itself, and the free-text path bypasses even that
   (responsible-ai, security, determinism).** §7 says "the LLM must never be the only gate"; the
   spike makes it exactly that (a sentence in `SYS_SFW`), and `type-your-own` reaches the GPU with
   **zero** filtering. *Fix:* a deterministic, external (non-generating-model) red-line +
   injection filter on a single chokepoint that **both** the LLM-proposed and free-text prompts
   must pass before parameterizing a workflow; fail-closed on match **or** error.

4. **B4 — Tree persistence is not crash/concurrency-safe (reversibility-tx, determinism).** The
   tree **is** the undo stack; `save_tree` does in-place `open(,'w')` (torn-write loses the whole
   history) and `step` is a minutes-long read-modify-write with no lock — two surfaces (panel +
   notification) collide on `counter+1` → lost node = un-revertable lost branch. *Fix:* the
   `feed.rs` temp+fsync+rename idiom + a per-session cross-process lock (or stale-read-proof
   max-id append).

## Prioritized path to a defensible 10/10 (leverage-ordered)

1. **Build the smallest lucid as a coordinator-dance validation harness, not a creative tool**
   (ai-product). MVP = a **linear chain** (drop the branching tree, quota, "delete my dreams"
   surface), driven through shipped `lease.rs`: `Acquire` beat-gen → confirmed evict → `Spawn`
   video → **prove a live Hermes chat preempts/SIGKILLs the dream and it fails open to the
   shader**. This clears B1's structure and validates the actual bet for the price of wiring.
2. **Make the two model-in-the-loop gates real code** (B2+B3): one chokepoint, deterministic,
   external, fail-closed, on text **and** image, covering LLM-proposed **and** free-text paths;
   bind a pinned structured-output schema with a pure, unit-tested validator (no live model).
3. **Make state crash/concurrency-safe and deletion complete** (B4 + reversibility): atomic tree
   write + per-session lock; "delete my dreams" reaches all three sinks (dreams cache,
   `output/lucid/`, `input/` Lucid-prefixed frames) via the tree as index of record; quota/TTL that
   respects wallpaper/tx pins.
4. **Wire the kill/keep metric before any UI** (ai-product): median chain length + abandon-after-
   first-beat, emitted to the keyhole. Without it, minutes-per-beat steering is an unfalsifiable bet
   that could be strictly worse than one-shot Surface B.
5. **Design the waits and close the TOCTOU** (interaction, determinism): choices carry identity
   (prompt + set-hash), executed beat provably equals displayed beat on panel **and** notification;
   beat-gen wait = "menu assembling," video wait = last-frame "developing" with the **keyhole as the
   honest progress surface**; humane timeout (~30 s, not 300 s); always-reachable Stop wired to the
   lease release.
6. **Defer** branching, the QML panel, VLM frame-grounding (§6), and "set as wallpaper" (needs the
   unbuilt ADR-0005 tx) behind the greenlight gate above.

## Spike evidence (this pass)

- Static: both spike files `py_compile` clean. Engine in-flight fixes (clip length 49→33 to match
  the workflow's baked length; `VHS_VideoCombine` API `filename_prefix` fix) committed.
- Live (LLM half only, 21.5 GB free / nothing co-resident — video step deliberately NOT run per
  B1): `start` → `beats` returned 4 distinct, schema-valid, frame-continuing beats. The steering
  contract works; the safety/coordination gates around it do not yet exist.

## Owed-reviewer list — additions

ADR-0014's list is strong but add: **wayland-computeruse-reviewer** (captured Plasma/KWin
wallpaper-plugin key-set completeness for the set-as-wallpaper tx; clean compositor reclaim on
SIGKILL mid-allocation), and **personalization-loop-reviewer** (if §6 frame-grounding / "story so
far" ever biases future beat proposals). Broaden reversibility-tx's scope from "wallpaper-apply
path" to also own the dream-tree's crash-safe/concurrent persistence and deletion completeness.
