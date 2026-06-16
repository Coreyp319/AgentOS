# Review 0005 — VRAM coordinator scorecard (4-reviewer panel)

- Status: Review complete (2026-06-16); **this-pass fixes applied** (R1, R2, H1, H2, H3, H5-partial
  + nits — 36 tests, clippy clean, R1/H1/H2 proven live). Deferred → [ADR-0013](../adr/0013-coordinator-ipc-trust-and-lease-lifecycle.md) (S1, H4, M4) and an ADR-0004 amend (H5 full)
- Subject: `crates/agentosd/src/{lease.rs,coord.rs}` at commits `5116bc6` / `4281458` / `e3c4b9d`
- Panel: resource-safety-reviewer · rust-performance-reviewer · security-reviewer · determinism-safety-reviewer
- Relates to: ADR-0003 (fail-open), 0004 (graphics-yield), 0006 (Hermes plugin/D-Bus), 0009, 0010

## Verdict

The **pure decision core is excellent** (all four reviewers): `admit` (saturating, total),
`arbitrate`, and `LeaseState` with **monotonic tokens** are pure, deterministic, exhaustively
unit-tested; no model output reaches the grant/deny/evict path. The **impure shell** that turns
those decisions into real VRAM reclaim has real holes. Two **Critical**, five **High**. Net:
the daemon is **not yet safe to install as a service or wire to a real GPU job** — but every gap
is fixable without touching the (good) core.

## Findings (deduped across reviewers)

| # | Sev | Finding | Owner lens | Disposition |
|---|-----|---------|-----------|-------------|
| S1 | **Critical** | `Spawn(argv)` over the **unauthenticated session bus** = arbitrary command execution as the user; any session peer (flatpak/browser/extension) can `Spawn … sh -c '…'`. No caller authz on any method. | security | **DEFER → ADR-0013** (IPC redesign); cheap mitigations now |
| R1 | **Critical** | SIGKILL of the owned `Child` only kills the **direct PID** — a wrapper/forking launcher leaves the CUDA-holding **grandchild** resident → VRAM not reclaimed → headline guarantee fails. | resource-safety | **FIX now** (process group + `kill(-pgid)`) |
| R2 | **Critical** | `free_mib().unwrap_or(0)` **inverts fail-open**: an NVML blip → `free=0` → admission **denies interactive** (cooperative `Acquire`), blocking live AI — opposite of ADR-0003. | resource-safety / determinism | **FIX now** |
| H1 | High | Preempt **skips admission AND never re-verifies fit** after eviction: interactive (maybe 20 GB) granted unconditionally though the victim it evicted may be 2 GB → OOM on the top-priority path. The decision is *incomplete*. | determinism + resource-safety | **FIX now** (two-stage gate) |
| H2 | High | `start_kill()` without `wait()` → transient **zombie**; the reclaim Δ is read after a fixed **300 ms guess**, not after actual exit → dishonest "reclaimed" proof. | rust + resource-safety | **FIX now** (reap before probe) |
| H3 | High | Preempt-then-`Spawn` is **not failure-atomic**: the victim is SIGKILLed *before* the successor spawn is confirmed; a bad argv destroys the incumbent for nothing. | rust + resource-safety | **FIX now** (spawn-before-kill) |
| H4 | High | **Lease-leak / no liveness**: a crashed cooperative holder (or a malicious `Acquire(interactive)`) holds the never-preemptible top tier **forever** → batch lane wedged. Also a security DoS. | resource-safety + security | **DEFER → ADR-0013** (lease lifecycle: peer-disconnect + TTL) |
| H5 | High | Caller `estimate_mib` **trusted**; flat 512 MiB headroom too thin — self-reported sizes undercount ~8% (19.5 GB measured for 18 GB-reported), KV scales with `NUM_PARALLEL` → `admit` GRANTs footprints that then OOM. | resource-safety | **PARTIAL now** (scaled headroom); full model → ADR amend |
| M1 | Med | TOCTOU: `free` read *outside* the lock, decision made *inside* → stale snapshot under concurrent calls (bounded by the arbitration re-check). | rust + resource-safety | FIX now (read under lock) |
| M2 | Med | `supervise()` has no explicit shutdown; owned child killed only via `kill_on_drop` drop-order at runtime teardown (implicit, untested). | rust | FIX now (explicit teardown) |
| M3 | Med | Spawn-failure after a preempt kills the incumbent with **no requeue signal** to its owner. | resource-safety | Folded into H3 |
| M4 | Med | No **hysteresis/dwell** → spawn→preempt→spawn **strobe** under bursty interactive load (ComfyUI relaunch is expensive). | resource-safety | DEFER → ADR-0013 |
| M5 | Med | Spawned child inherits the daemon's **full env / cwd / fds**; no `.env_clear()`/`current_dir`/`stdin(null)`. | security | **FIX now** |
| M6 | Med | `coord` SIGUSR1 = same-uid preempt DoS (demo-only; never add a signal control path to the daemon). | security | Document |
| L* | Low | `pid unwrap_or(0)`; swallowed `start_kill` error; `expect()` on supervised paths; hot-path `String` alloc; relative `argv[0]` PATH; argv-size cap; `cargo audit` not in CI; ADR "gated by that math" over-promises the preempt path. | all | FIX the cheap ones now; rest noted |

## Strengths (unanimous)

- Pure/impure split is real; `admit`/`arbitrate`/`LeaseState` are pure, total, and exhaustively
  tested (33 tests). The **stale-release-can't-free-a-successor** monotonic-token guard is exactly
  right and a class of bug many miss.
- `coord`'s `select!` cancel-safety is correct (only the wait arm borrows the child; SIGKILL after
  the loop); `Child::wait` is cancel-safe in tokio 1.52.
- NVML correctly behind `spawn_blocking`; `Arc<Nvml>` sharing sound (`Nvml: Send + Sync`).
- `kill_on_drop(true)` as a daemon-death fail-safe; `default-features = false` on zbus.

## Fix plan

**This pass (clear correctness/safety, no IPC-contract change):** R1, R2, H1, H2, H3, H5-partial,
M1, M2, M5, and the cheap Lows (start_kill error, `expect`→structural, scaled headroom, `write!`).

**Deferred → ADR-0013 (needs a design decision):**
- **S1 + H4 IPC trust model.** Move off the open session-bus well-known name to a **private
  `0600` peer socket** under `$XDG_RUNTIME_DIR` with an `SO_PEERCRED` uid check, **and** replace
  caller-supplied `argv` with a **static allowlist of launch profiles** (`Spawn(tier, est,
  profile, params)` — the daemon owns the command vector). Add **peer-disconnect auto-release +
  lease TTL** so a crashed/buggy holder can't wedge the lane. This changes the ADR-0006
  Hermes-plugin call contract → Corey's call.
- **H5 full admission model** (multiplicative fudge + per-`NUM_PARALLEL` KV term) → amend ADR-0004/0010.
- **M4 hysteresis/dwell** between preempt and re-acquire.

**Doc precision:** amend ADR-0010 §Consequences — the *grant* is gated by VRAM math; the
*preempt+kill of the lower tier* is gated by **priority**, with a now-computed fit verdict surfaced
to the successor (it no longer silently claims a fit it never checked).
