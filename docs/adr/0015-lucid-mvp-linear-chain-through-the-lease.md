# ADR-0015: Lucid MVP — a linear chain through the coordinator lease (narrows ADR-0014)

- Status: Proposed
- Date: 2026-06-16
- Narrows: [ADR-0014](0014-lucid-interactive-branching-dream-loop.md). Does not supersede it — the
  branching tree, QML panel, notification-as-control, §6 VLM frame-grounding, and "set as wallpaper"
  remain the target design; this ADR defines the **smallest buildable slice** that proves the bet
  and clears the safety blockers before any of that is built.
- Driven by: the design-council scorecard [0008](../research/0008-lucid-review-scorecard.md)
  (verdict: HOLD-as-ship / ITERATE-as-spike; blockers B1 co-residency OOM, B2 real-person seed→i2v,
  B3 LLM-only red-line, B4 non-atomic tree). The ai-product steer: *build a linear chain through the
  real lease and prove the coordinator dance — that is the differentiated bet the spike skipped.*
- Relates to: ADR-0001 (reuse), 0003 (fail-open), 0006/0010/0013 (coordinator), 0009 §3
  (mutual-exclusion / live inference outranks the dream). Reuses the `dream.sh` lease-client pattern.

## Context

The throwaway spike (`lucid_engine.py`) proved the *commoditized* half — ComfyUI+Ollama last-frame
chaining — and skipped the *differentiated, risky* half: the GPU turn-taking that **is** the
product ("the loop is the coordinator dance," ADR-0014 §Context). It also runs the ~21.8 GB i2v
step with **no lease**, relying on `keep_alive:0` (a fire-and-forget hint, the `/free` mistake) to
avoid co-residency — which the council flagged as a desktop-OOM blocker (B1).

## Decision

**The MVP is a linear, append-only chain, generated through the coordinator lease, with the model
gates as real code.** Five rules:

1. **Linear, not branching.** State is `chain.json` — an append-only list of nodes
   `{id, parent=prev, label, prompt, seed, clip, out_frame}` — written **atomically** (temp +
   `os.replace`, the `feed.rs` idiom), under a per-session lock. No tree, no reroll, no scrub-back,
   no quota/"delete my dreams" surface (a linear chain is `rm -rf <session>`). Branching is a
   fast-follow gated on "did users chain ≥3 beats and ask to go back?" *(clears B4; defers the tree.)*

2. **The video step goes through the lease.** Reuse the `dream.sh` client verbatim: ask agentosd to
   `Spawn` + own ComfyUI under the **batch** tier (predict-before-load admission); generate one i2v
   clip; `Release` → agentosd SIGKILLs the owned ComfyUI → VRAM reclaimed. If the coordinator is
   unreachable or refuses (GPU busy), **fail open** (ADR-0003): the beat is skipped, never forced.
   *(clears B1's admission half; the dream is now a governed, evictable job.)*

3. **Beat-gen confirms eviction before the video acquires.** `keep_alive:0` is demoted to a hint.
   After beat-gen, poll Ollama `/api/ps` (and/or NVML free) until the beat model is **confirmed
   absent** (with timeout) *before* requesting the video lease. Preferred end state: move beat-gen
   to a small CPU/≤2 GB model so it is never a VRAM event at all (owed). *(clears B1's eviction half.)*

4. **Two model gates are deterministic code, external to the generating model, on BOTH paths.**
   Every prompt — LLM-proposed **and** type-your-own — passes one chokepoint before it can
   parameterize a workflow: (a) a **total schema validator** (`{beats:[{label≤40, prompt≤400}]}`,
   reject non-conforming → degrade to type-your-own), and (b) a **code-side red-line filter**
   (fail-closed on match or error). The LLM is never the only gate. *(clears B3; text half of B2.)*

5. **Preemption is the heartbeat, surfaced honestly.** Live interactive inference outranks the
   dream (ADR-0009 §3): an `Acquire(interactive)` arriving mid-clip makes agentosd SIGKILL the owned
   ComfyUI; the in-flight clip is lost (a cache artifact only) and the loop reports "paused — you're
   chatting; the dream waits," then resumes when the GPU frees. No co-residency, ever.

## Still owed (explicitly deferred, not done by this MVP)

- **B2 image-side likeness guard** — face/person detection on the seed + each anchor frame
  (real-face default-block; real-face + adult-tone hard-refused). The MVP guards prompt **text**;
  the **image** vector is owed to `responsible-ai-privacy-skeptic` + `security-reviewer` before any
  real-person seed is allowed. The runner exposes a `seed_image_guard` hook that is **fail-closed
  off** (rejects unknown seeds unless `LUCID_ALLOW_UNVETTED_SEED=1` for spike testing).
- The red-line **term list** is a conservative starting set owed to RAI/security for the real content.
- ComfyUI **warm-keep** across consecutive turns (the 17 GB reload tax) — MVP re-spawns per beat for
  correctness; warm-keep is the latency fast-follow (resource-safety).
- The QML panel, notification-as-control, branching, §6 grounding, and "set as wallpaper" (needs the
  unbuilt ADR-0005 tx) — all behind the greenlight gate below.

## Greenlight gate (to graduate past this MVP)

1. The dance is validated live: `Acquire`/confirm-evict → `Spawn` video → a real Hermes chat
   **preempts/SIGKILLs** the dream and it fails open to the shader.
2. The kill/keep metric is wired (median chain length / abandon-after-first-beat → keyhole).
3. B2 (image likeness guard) is an accepted, implemented design.

## Consequences

- Evidence: `spikes/dreaming/lucid/{lucid_safety.py, lucid_linear.py, test_lucid_safety.py}` — the
  pure gates are unit-tested without a model/GPU/daemon; the lease dance is provable via `dream.sh`-style
  test seams (`LUCID_LAUNCHER`/`LUCID_GEN_CMD`) and falls open cleanly when the daemon is down.
- This MVP is still `[SUBSTRATE-BLOCKED]` on the coordinator for live use, but — unlike the original
  spike — it is **on the right side of the safety line**: governed, evictable, gated, fail-open.
