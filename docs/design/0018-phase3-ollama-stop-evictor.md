# Phase 3 design — the `ollama stop` graceful evictor (ADR-0018)

Status: **pre-staged spec, NOT built.** Implement only when the go/no-go signals justify it (below).
This is the narrow, highest-value Phase-3 slice: let the **heavy lane** (a ComfyUI/dream job, the RT
wallpaper) reclaim VRAM that a *lingering warm Ollama model* holds — the one thing Ollama won't do
for a non-Ollama consumer, and which agentosd cannot do with SIGKILL (it doesn't own Ollama's PID).
It is independent of multi-model coexistence: it earns its place even if `MAX_LOADED=1`.

## Why this and not "coexistence-aware admission"
The review panel (ADR-0018 §criteria 1) was explicit: do NOT build a warm-set accountant — that
re-implements Ollama residency and drifts from NVML. Admission stays **scalar + live-NVML**; `free_mib`
already reflects whatever Ollama holds. This slice adds exactly one new capability: a *graceful evict
lever for the warm pool* that runs before the existing SIGKILL backstop.

## Build gate (when to implement)
From `agentosd coexist` over ≥3–7 days of real use:
- **Build it** if `oom_danger_ticks > 0` while a heavy job wanted the GPU, OR you observe a dream/ComfyUI
  start blocked/denied while an Ollama model was resident (free VRAM held by the 30-min keep-alive).
- **Don't build it** if heavy jobs never contend with warm models (then the existing lease is enough).
- Note: `avoided_swaps`/coexistence signals justify the *warm-pool* path (config tuning), NOT this
  evictor. This evictor is gated on *heavy-lane-vs-warm-model* contention specifically.

## Algorithm (the measure-don't-predict flow)
On a heavy-lane `Acquire`/`Spawn` whose admission falls short of `est + headroom`, BEFORE granting or
SIGKILLing, attempt graceful warm-pool reclaim:

```
1. read free_mib (NVML, spawn_blocking — never block the reactor; see coord::free_mib)
2. if admit(free, est, headroom) already grants → done, no reclaim needed
3. query /api/ps; pick the COLDEST resident model (max loaded_secs / least-recently-used)
4. POST /api/generate {model, keep_alive:0}  (or `ollama stop <model>`) — graceful unload
5. POLL free_mib until it RISES by ~the expected reclaim OR /api/ps shows the model gone,
   bounded by a timeout (≈2–3s, a few spawn_blocking reads). DO NOT predict from the learned
   footprint — the learned number is only the poll TARGET. (agentosd owns no PID here, so there
   is no child.wait(); polling is the only honest signal.)
6. re-run the REAL admit(measured_free, est, headroom):
      - grants → proceed (cooperative: return token; owned: spawn child)
      - still short and more cold models exist → repeat from 3 (one at a time)
      - still short, pool exhausted → DENY (fail-closed for batch; ADR-0003). Do NOT SIGKILL the
        Ollama daemon — that would take down the whole runtime, not one model.
7. SIGKILL remains the backstop ONLY for an owned heavy holder being preempted by a higher tier
   (unchanged from ADR-0010); it is never a lever against the non-owned warm pool.
```

## Integration points in `crates/agentosd/src/lease.rs`
- Hook inside `LeaseState::acquire` / the `Acquire`/`Spawn` D-Bus handlers, AFTER arbitration decides
  the requester wins on tier but BEFORE the grant — mirror where `fits_after_evict` is consulted today.
- Run the reclaim **under the `Inner` mutex** (or with a token guard) so the stop→poll→re-admit is
  atomic w.r.t. the lease decision — no TOCTOU (determinism review §5). The poll's `free_mib` reads are
  `spawn_blocking`; hold the decision logically, not the executor.
- Reuse `admit` / `headroom_for` / `free_mib` unchanged — the deterministic core is the gate.
- Anti-strobe: extend the existing `cooldown`/dwell so a just-stopped model isn't eagerly re-warmed and
  a just-released heavy job doesn't immediately re-trigger a stop (ADR-0018 §criteria 6).

## Inputs from `coexist` (already built)
- The **clamped `admission_mib`** per model (floored to `size_vram`, max-not-median, sample-gated) is the
  `est` for a big-model swap and the expected-reclaim poll target. Never reserve below `size_vram`.
- A learned footprint is valid only for the `NUM_PARALLEL` it was measured under — relearn on change.

## Safety invariants (acceptance criteria — all must hold)
1. No grant without a post-stop **measured** re-admit against live free VRAM.
2. Warm-pool eviction is `ollama stop` only; never SIGKILL the Ollama daemon.
3. Batch/best-effort fail **closed** when free VRAM is unknown or the pool won't yield enough.
4. The learned footprint can only ever *raise* a reservation (clamp already enforced in `analyze.rs`).
5. Reclaim is atomic with the lease decision (no TOCTOU); reads off the reactor.
6. Anti-strobe dwell prevents stop/re-warm flapping.
7. Reversible: a stopped model reloads on next request; nothing is destroyed.

## Test plan
- Pure: `pick_coldest(residency) -> name`; `reclaim_target(est, free) -> needed`.
- Integration (busctl-driven, like the existing lease smoke tests): warm model resident → heavy `Spawn`
  short on VRAM → assert `ollama stop` issued, free re-read, then grant; pool-exhausted → assert DENY,
  no daemon kill; owned-holder preempt path unchanged (still SIGKILLs the owned child only).
