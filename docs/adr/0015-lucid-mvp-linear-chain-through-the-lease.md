# ADR-0015: Lucid MVP — a linear chain through the coordinator lease (narrows ADR-0014)

- Status: Proposed
- Date: 2026-06-16
- Amended: 2026-06-16 — rule 3 (B1 eviction) given teeth, AND the beat model moved off the heavy
  9.6 GB gemma4. The confirm-only gate timed out **every turn** (the web surface keeps the beat
  model warm — each page view reloads it via `/api/beats`), so the dream perpetually skipped. Two
  changes: (a) B1 now *actively* `ollama stop`s the beat model (ADR-0018's measured lever) before
  confirming (`lucid_safety.force_evict`); (b) beat-gen moved from gemma4:latest (9.6 GB, slow +
  observed to *wedge* in `Stopping…` past 120 s) to the small registry "narrator" model
  qwen2.5vl:3b — measured to free in **~3 s**, comfortably inside the 30 s gate, and small enough to
  coexist with the lighter video models. Verified: the full pipeline reaches `done` and the React
  player plays the clip (run with a manual evict); `force_evict` unit-tested; the small-model
  eviction timed live. The single end-to-end hands-free run on the new config is owed (the GPU was
  busy with other work at amend time).
- Amended: 2026-06-16 — **held per-frame beat menu** (the "no reroll" half of rule 1, made real). The
  "what happens next" suggestions were re-rolled by the non-deterministic narrator on *every*
  `/api/beats` read; the only thing holding them steady was a client cache (React Query / a JS global)
  keyed on chain **length** with a 60 s GC. So a reload, a second tab, or — most commonly — a
  `skipped`/`error`/`refused` turn after a multi-minute dream silently swapped the menu under the user,
  and a same-length frame of a *prior* dream collided with the current one after burn→restart. Fix:
  the menu is rolled **once per chain tip** and persisted on the tip node (`nodes[-1].beats`, an
  optional `[{label, prompt}]`) through the same atomic `save_chain`; `/api/beats` re-serves it
  verbatim and is frozen while a beat is in flight (a transient empty roll is never sealed). The tip's
  `beats` is the **one late-bound write** to an otherwise append-only node — sealed once a child node
  is appended. "Model proposes, code disposes" (rule 4) now holds for *which options are offered*, not
  only for *what a click generates*. Evidence: `lucid_linear.beats_for_tip` + `test_lucid_linear.py`.
- Amended: 2026-06-16 — **rule 2 made real + warm-keep landed (the keyhole LEASE was perpetually
  empty during dreams).** The always-on `comfyui.service` silently DEFEATED rule 2: with ComfyUI
  already bound to `:8188`, the coordinator's `Spawn`'d `start-comfyui.sh` lost the port race, died
  in ~1 s, and the supervisor auto-released the lease (~750 ms) — while the real dream compute ran on
  the always-on instance, **unleased and un-preemptible** (the eviction dance was a no-op for
  dreaming; the keyhole honestly showed `LEASE —` + `WORKLOAD ComfyUI · NN GB` at once). Fix, three
  parts: (a) **always-on `comfyui.service` is disabled** — ComfyUI is now coordinator-owned
  on-demand (the architecture already supported it: `dream.sh` and `lucid_linear` both spawn via the
  lease, and `readiness.can_dream` no longer gates on ComfyUI being pre-up); a **port-race guard** in
  `start-comfyui.sh` refuses (exit 3) if `:8188` is already answering, so a stray instance fails
  loud-open instead of silently re-introducing the bug. (b) **Warm-keep is implemented** (the "Still
  owed" reload-tax item below): `lucid_web` holds ONE batch lease across a session's beats
  (`external_lease=True` → `step`/`generate_video` neither Spawn nor Release), spawning ComfyUI once
  and releasing on a fresh `/api/start`, burn/delete, a 10-min idle reaper, or shutdown (the
  coordinator owns ComfyUI independently of the web process, so an un-released lease would leak
  ~17 GB). (c) the owned-job **holder label maps to `comfyui`** so the tray reads `batch (comfyui)`.
  Verified live: a `/api/dream` spawned ComfyUI on-demand and the keyhole read `LEASE batch
  (comfyui)` for the whole beat; a one-shot `Acquire(interactive)` **preempted token 1 (fits) →
  SIGKILLed** the owned ComfyUI (`:8188` freed, VRAM reclaimed) — greenlight gate 1's preempt half,
  mechanically proven. Warm-keep invariants unit-tested (`test_lucid_warmkeep.py`, 4 green) + a real-
  coordinator sleep-profile smoke (one Spawn, token reuse, release). A full hands-free multi-beat
  video run remains owed (same as the first amend note). Edge: a hard `SIGKILL` of `lucid_web` (not
  the systemd `SIGTERM`) leaks the held lease until the daemon restarts — the idle reaper covers the
  common walked-away case.
- Amended: 2026-06-16 — **stale-worker epoch guard** (the in-flight turn machine made race-safe). A
  beat runs minutes on a daemon thread; a `/api/start`, `/api/delete`, or burn arriving mid-beat used
  to let the finishing worker (a) clobber the fresh `idle` with a stale `done`/`error` and (b) — worse
  — re-`save_chain` its stale in-memory chain, **resurrecting a just-deleted (possibly private) dream**
  on disk. Fix: a monotonic `TURN["epoch"]`; `/api/dream` captures it, the three session-resetting
  endpoints bump it (`_supersede_turn`), and the worker discards BOTH writes when superseded — its
  terminal `TURN.update` and, via an `is_current` predicate threaded into `lucid_linear.step`, its
  chain persist (the clip is dropped as a cache artifact, exactly like a preempt). No reject-while-busy
  needed, so restarting/deleting mid-dream stays instant. Evidence: `test_lucid_warmkeep.EpochGuard`
  + `test_lucid_linear.StepSupersedeTest`. (Also this pass: the B2 real-person likeness consent moved
  from a native `confirm()` to an in-surface warm consent card — the one "needs you" cue.)
- Amended: 2026-06-16 — **epoch guard extended from state writes to the GPU lease (review pass).** A
  code-review of the working tree found the epoch gated the *chain/TURN* writes but NOT the warm-keep
  lease side-effects, leaving a real ~17 GB leak: a worker that cleared the epoch check, then had a
  burn/delete's `_release_lease` run past it, would `_ensure_lease` a fresh ComfyUI for a now-dead
  session — unreclaimable until the 600 s idle reaper. Fix: `_ensure_lease(epoch)` re-checks the epoch
  (lock-free GIL-atomic read — taking `TURN_LOCK` under `LEASE_LOCK` would break the flat-lock rule) at
  every commit point and never leaves a token held for a superseded turn; the post-beat deadline touch
  is likewise epoch-gated so a stale worker can't push a new session's idle clock forward. Burn/delete
  teardown centralized in `_end_session()` (supersede-then-release, one home for the invariant). Web
  surface: the held-menu cache is now `removeQueries`-evicted (not just invalidated) on start/burn/
  delete — node ids restart at 0 each dream so the tip key collides and `invalidate` flashed the prior
  dream's stale suggestions; the beat menu locks on a *successful* fire until `<Dreaming/>` mounts
  (closing a ~2.5 s double-fire window); and swapping the seed image re-opens the B2 consent gate (a
  consent granted for one likeness must not ride along with a different upload). Also: `nosniff` on the
  static bundle now that arbitrary JS/SVG is served. **Accepted residuals (documented, not fixed):**
  (a) the `is_current()`→`save_chain` and `beats_for_tip` read-modify-write windows on `chain.json`
  remain non-atomic vs a concurrent reset — in practice `_release_lease` SIGKILLs ComfyUI so generation
  fails first; a shared `SESSION_WRITE_LOCK` would close it fully; (b) a privacy burn can block up to
  `READY_TIMEOUT` (180 s) behind a first-cold-start `_ensure_lease` holding `LEASE_LOCK` — narrow
  (only during the session's initial spawn) and reordering the wipe risks a ComfyUI-rewrites-the-dir
  race, so left as-is.
- Narrows: [ADR-0014](0014-lucid-interactive-branching-dream-loop.md). Does not supersede it — the
  branching tree, QML panel, notification-as-control, §6 VLM frame-grounding, and "set as wallpaper"
  remain the target design; this ADR defines the **smallest buildable slice** that proves the bet
  and clears the safety blockers before any of that is built.
- Driven by: the design-council scorecard [0008](../research/0008-lucid-review-scorecard.md)
  (verdict: HOLD-as-ship / ITERATE-as-spike; blockers B1 co-residency OOM, B2 real-person seed→i2v,
  B3 LLM-only red-line, B4 non-atomic tree). The ai-product steer: *build a linear chain through the
  real lease and prove the coordinator dance — that is the differentiated bet the spike skipped.*
- Relates to: ADR-0001 (reuse), 0003 (fail-open), 0006/0010/0013 (coordinator), 0009 §3
  (mutual-exclusion / live inference outranks the dream), 0018 (VRAM coexistence — the measured
  `ollama stop` lever B1's active evict now uses). Reuses the `dream.sh` lease-client pattern.

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
   `{id, parent=prev, label, prompt, seed, clip, out_frame}` (plus an optional `beats` — the held
   menu for that frame; see the held-per-frame-beat-menu amendment) — written **atomically** (temp +
   `os.replace`, the `feed.rs` idiom), under a per-session lock. No tree, no reroll, no scrub-back,
   no quota/"delete my dreams" surface (a linear chain is `rm -rf <session>`). Branching is a
   fast-follow gated on "did users chain ≥3 beats and ask to go back?" *(clears B4; defers the tree.)*

2. **The video step goes through the lease.** Reuse the `dream.sh` client verbatim: ask agentosd to
   `Spawn` + own ComfyUI under the **batch** tier (predict-before-load admission); generate one i2v
   clip; `Release` → agentosd SIGKILLs the owned ComfyUI → VRAM reclaimed. If the coordinator is
   unreachable or refuses (GPU busy), **fail open** (ADR-0003): the beat is skipped, never forced.
   *(clears B1's admission half; the dream is now a governed, evictable job.)*

3. **Beat-gen actively evicts the beat model, then confirms, before the video acquires.**
   `keep_alive:0` is demoted to a hint — and it was observed *not to land*: the web surface keeps the
   beat model warm (every page view reloads it via `/api/beats`), so the confirm-only gate timed out
   every turn and the dream perpetually skipped. So B1 now *forces* the release with the lever
   ADR-0018 measured as effective — `ollama stop` (a `keep_alive:0` unload request; `POST /free`
   freed 0 MiB and is not on this path) — then polls Ollama `/api/ps` until the beat model is
   **confirmed absent** (with timeout) *before* requesting the video lease. Still fail-closed: an
   unreachable Ollama, a refused stop, or a model another consumer keeps reloading refuses the video
   (never co-resident with the 17–21 GB i2v step, which ADR-0018 finds is effectively exclusive on a
   24 GB card). Preferred end state still owed: move beat-gen to a small CPU/≤2 GB model so it is
   never a VRAM event at all. *(clears B1's eviction half — now with teeth.)*

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
- ~~ComfyUI **warm-keep** across consecutive turns (the 17 GB reload tax) — MVP re-spawns per beat for
  correctness; warm-keep is the latency fast-follow (resource-safety).~~ **DONE (2026-06-16 amend):**
  `lucid_web` holds ONE batch lease across a session's beats and releases it on session-end / idle /
  shutdown; ComfyUI is coordinator-owned on-demand (always-on `comfyui.service` disabled, the cause
  of the perpetually-empty keyhole LEASE). Only the first beat of a session pays cold-start.
- The QML panel, notification-as-control, branching, §6 grounding, and "set as wallpaper" (needs the
  unbuilt ADR-0005 tx) — all behind the greenlight gate below.

## Greenlight gate (to graduate past this MVP)

1. The dance is validated live: `Acquire`/confirm-evict → `Spawn` video → a real Hermes chat
   **preempts/SIGKILLs** the dream and it fails open to the shader. *(Preempt/SIGKILL half proven
   2026-06-16: a live `Acquire(interactive)` evicted the owned ComfyUI mid-hold, VRAM reclaimed,
   `LEASE` cleared. Still owed: the full hands-free multi-beat run that fails open to the shader.)*
2. The kill/keep metric is wired (median chain length / abandon-after-first-beat → keyhole).
3. B2 (image likeness guard) is an accepted, implemented design.

## Consequences

- Evidence: `spikes/dreaming/lucid/{lucid_safety.py, lucid_linear.py, test_lucid_safety.py}` — the
  pure gates are unit-tested without a model/GPU/daemon; the lease dance is provable via `dream.sh`-style
  test seams (`LUCID_LAUNCHER`/`LUCID_GEN_CMD`) and falls open cleanly when the daemon is down.
- This MVP is still `[SUBSTRATE-BLOCKED]` on the coordinator for live use, but — unlike the original
  spike — it is **on the right side of the safety line**: governed, evictable, gated, fail-open.
