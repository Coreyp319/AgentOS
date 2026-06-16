# 0006 — Hermes GPU-coordinator plugin: design brief (ADR-0006)

- Status: Design brief, pre-build (pressure-test before TDD)
- Date: 2026-06-16
- Implements: ADR-0006 (Hermes integration via a plugin, not a fork)
- Depends on: ADR-0010 (VRAM coordinator), ADR-0013 (IPC trust / lease lifecycle),
  ADR-0003 (fail-open-supervised)

## Goal
Make the agentosd VRAM lease **actually used** by Hermes so that **interactive inference
preempts overnight batch/dreaming** — without serializing Hermes's own concurrent
inferences, and without ever blocking live AI when the coordinator is down (fail-open).

This is a **Hermes plugin** (Python, lives in `~/.hermes/plugins/`), NOT a fork and NOT
in this repo's Rust tree. The agentosd side already exists and has teeth (`agentosd lease`).

## Wire contract (already shipped, do not change here)
`org.agentos.Coordinator1` on the **session bus**, object `/org/agentos/Coordinator1`:

| Method | Signature | Returns | Semantics |
|---|---|---|---|
| `Acquire` | `(tier:s, estimate_mib:u)` | `(granted:b, token:t, msg:s)` | **cooperative**, caller owns its process. **Non-blocking**: returns `Granted`/`Queued`/`Denied` immediately. Higher tier preempts a lower **owned** holder (SIGKILL). |
| `Spawn` | `(tier:s, est:u, profile:s, params:as)` | `(b, t, s)` | owned (agentosd spawns+evicts). Not used by this plugin. |
| `Release` | `(token:t)` | `b` | true iff `token` is the current holder. |
| `Renew` | `(token:t)` | `b` | extend the holder's TTL (backstop default 5400 s). |
| `Status` | `()` | `(held:b, tier:s, token:t, free_mib:u)` | live lease + free VRAM. |

Tiers: `interactive`/`live` (top), `batch`/`overnight`, `best-effort`/`idle`.
Hermes inference → **`interactive`**. Dreaming/ComfyUI → `batch`.

## The crux: exclusive lease vs. concurrent inference
The lease is **strictly exclusive (single holder token)** and `Acquire` is **non-blocking**.
Hermes runs **many concurrent inferences** (gateway + subagents). Acquiring the lease
*per call* would serialize all Hermes inference to one-at-a-time — wrong.

**Decision — process-wide refcount.** Hold the interactive lease while ≥1 interactive
inference is in flight; release when the last finishes. Concurrent calls share one token.

```
on_llm_execution(request, next_call, **ctx):
    holding = _enter_lease()        # refcount 0→1: Acquire(interactive, est); else just ++
    try:
        return next_call(request)   # the actual provider call (single-use, must call once)
    finally:
        _exit_lease(holding)        # refcount 1→0: Release(token)
```

- `_enter_lease()` (under a module lock): if `refcount == 0`, call `Acquire`. On **Granted**,
  store `token`, start the renew thread. On **not-granted / unreachable / timeout** →
  **fail-open**: `token = None` but still proceed. Always `refcount += 1`.
- `_exit_lease()` (under lock): `refcount -= 1`; if `refcount == 0` and `token`, `Release(token)`
  + stop renewer + `token = None`.

Effect: while any interactive inference runs, the interactive lease is held → batch
(ComfyUI dream) is preempted/cannot start. Last release reopens the batch lane. No
serialization of interactive calls against each other.

## The three integration points (all via `register(ctx)`)
1. `register_middleware("llm_execution", on_llm_execution)` — the refcounted Acquire/Release
   wrap above. **This is the one with teeth.**
2. `register_middleware("llm_request", on_llm_request)` — attach `X-GPU-Priority: <tier>`
   to the request kwargs (returns `{"request": <kwargs>}`). **Best-effort**: the proxy may
   ignore it today (ADR-0006 consequence: "priority is best-effort, not preemptive").
3. `register_hook("pre_tool_call", on_pre_tool_call)` — **soft veto** on child-spawning tools
   (`delegate_task`) when the GPU is saturated. Returns `{"action":"block","message":...}`
   or `None`. Best-effort backpressure (true spawn-gating has no hook — ADR-0006).

## Non-negotiables baked in
- **Fail-open (ADR-0003).** Every D-Bus interaction has a short timeout; on ANY failure
  (daemon down, queued, denied, parse error, timeout) → log once, proceed. Never block
  live AI on the coordinator. The veto also fails open (Status unreachable → allow).
- **Thread-safe.** The gateway is concurrent; all refcount/token state under one lock.
- **Opt-in + kill-switch.** Standalone plugin, enabled via `hermes plugins enable`; a global
  env kill-switch (`AGENTOS_GPU_COORD_DISABLE=1`) makes every hook inert.
- **Idempotent / monotonic.** Release is token-guarded daemon-side; a stale release is a no-op.

## Transport — `busctl` subprocess (v1), abstracted
The Hermes venv has no `jeepney`/`dasbus`/`pydbus`. `busctl --user` is present and is exactly
how the daemon is already driven. v1 = `busctl --user --timeout=<T> call org.agentos.Coordinator1
/org/agentos/Coordinator1 org.agentos.Coordinator1 <Method> <sig> <args...>`, parse the typed
reply, nonzero/timeout/parse-fail → "unreachable" → fail-open. Wrapped in a `LeaseClient`
class so ADR-0013 **A1** (private peer socket + SO_PEERCRED) can swap the transport later
without touching hook logic. Refcount coalescing means Acquire/Release fire only on
0↔1 transitions + periodic Renew — subprocess fork cost is off the per-call hot path.

## Open decisions for the panel
1. **Refcount granularity / strobe.** Bursty traffic flapping refcount 1→0→1 could
   strobe the batch lane (release → batch restarts → next call preempts it). The daemon's
   C7 anti-strobe dwell (default 8 s) mitigates batch re-acquire, but should the plugin add
   a **linger** (hold the lease N seconds after refcount hits 0) to damp churn? Trade-off:
   linger keeps the GPU "reserved" for interactive, delaying dream restarts.
2. **Cold-start latency.** First interactive call's `Acquire` round-trip adds to first-token
   latency. Acceptable, or acquire optimistically/async and let the first call proceed while
   the Acquire is in flight?
3. **Estimate.** `estimate_mib` for interactive: fixed env value vs. model-aware. Interactive
   is top-tier + fails open, so the estimate mostly informs *batch* admission, not interactive
   grants. Default: a configured rough value (`AGENTOS_GPU_ESTIMATE_MIB`), keep simple.
4. **Veto threshold + latency.** "Saturated" = `Status().free_mib < AGENTOS_SPAWN_MIN_FREE_MIB`.
   This adds a `Status` call per `delegate_task`. Cache Status ~1–2 s. Is a soft veto even
   worth the surface, or defer point 3 entirely for v1?
5. **Priority header now or later?** Build the best-effort `X-GPU-Priority` tag now (cheap,
   forward-looking) or defer until the proxy honors it?
6. **Renew cadence.** TTL backstop is 5400 s. A long-held refcount (busy gateway) needs
   periodic `Renew`. Cadence ~TTL/3. Confirm the renewer can't outlive the held lease.

## Recommendation going in
Ship **v1 = points 1+2 (llm_execution refcount wrap + llm_request priority tag)**, fail-open,
busctl transport, opt-in. Treat point 3 (pre_tool_call veto) as **v1.1** behind its own env
flag — it has the weakest payoff (best-effort, adds latency) and the most false-positive risk.
TDD the pure decision functions (`_enter_lease`/`_exit_lease` refcount algebra, reply parsing,
saturation predicate) with a thin impure busctl shell — mirroring `lease.rs`'s
`admit`/`arbitrate` + thin-shell structure.
