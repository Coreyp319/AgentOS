# 0007 — Hermes GPU-coordinator plugin: 5-reviewer panel scorecard

- Status: Review complete; design locked for build
- Date: 2026-06-16
- Reviews the brief: `docs/research/0006-hermes-plugin-design-brief.md`
- Panel (Agent subagents, read-only advisory): resource-safety, determinism-safety,
  security, ai-generation, ai-product. Mirrors the ad-hoc specialist pattern of the
  coordinator review (`0005`); a focused substrate panel was chosen over the full
  design-council because this is a backend safety component, not a visual/market one.

## Verdict
**Ship — but a notch smaller than the brief.** The refcounted `llm_execution` wrap is the
whole product and the right design; everything else in the brief is forward-looking
decoration on an unproven E2E chain. All five endorse the core; the disagreements were
about *how much else* to ship (answer: nothing else in v1).

Determinism-safety verified the load-bearing fact: `on_llm_execution` runs on **plain OS
threads** (gateway workers + subagent `ThreadPoolExecutor`; `conversation_loop.py:1010`
invokes the chain synchronously), **not** the event loop. So `threading.Lock` is the
correct primitive and the feared await-across-the-lock deadlock cannot happen. No async
redesign needed.

## Convergence (≥4 of 5)
1. **Refcount-under-one-lock is sound.** Keep it; it solves exclusive-lease-vs-concurrent-
   inference correctly (Acquire/Release on 0↔1 transitions only, share one token).
2. **Cut the `pre_tool_call` veto from v1** (unanimous). Reinvents gateway backpressure
   (ADR-0006 says that's free); trusts a spoofable session-bus `Status` to *remove* an
   agent capability (a same-uid name-squatter answering `free_mib=0` wedges all delegation);
   highest false-positive surface; the lease wrap already wins the contention it targets.
   Defer **indefinitely**, re-enters only on production evidence + behind its own flag + A1.
3. **Fail-open is correctly scoped to scheduling, not isolation** — every IPC failure
   degrades to "live AI runs." Backed by the runtime: Hermes swallows raising hooks
   (`plugins.py:1674-1685`) and ignores malformed block returns (`:1890-1899`).
4. **The plugin only proposes (priority) / requests (Acquire/Release); every kill is
   disposed daemon-side** behind the monotonic-token guard. "Model proposes, code disposes"
   is honored — no model output reaches a mutation on the plugin side.

## The one disagreement: the `X-GPU-Priority` tag
- ai-generation / resource-safety: ship now as a labeled no-op (cheap, appears in
  `middleware_trace`, zero latency, forward-looking).
- ai-product (verified): **there is no proxy at all** — no `axum`/`hyper` in
  `crates/agentosd/Cargo.toml`, zero readers of the header anywhere. Not "best-effort" —
  *dead code that reads as a feature* and bakes a false success signal into the demo. The
  ADR-0002 enforcing proxy that would read it is unbuilt; even then ADR-0006 concedes
  priority is best-effort, not preemptive.
- **Resolution: CUT from v1.** Leave a commented, *unregistered* `on_llm_request` stub
  documenting the dependency ("# v1.1: needs the enforcing proxy (ADR-0002) to read this")
  so the seam is visible without a no-op masquerading as a feature. Re-register the day the
  proxy lands *and reads it* — that's its own work item.

## MUST-FIX before TDD locks the algebra
| # | Fix | Source | Disposition |
|---|-----|--------|-------------|
| M1 | **Self-heal on token loss.** `Renew`/`Release` returning `false` while refcount>0 (TTL expiry OR daemon restart) must trigger a **re-Acquire**, not a retained dead token. One mechanism closes daemon-restart + TTL-expiry + renewer-death. Without it the lane silently de-serializes → dream runs against live inference. | resource-safety M1, security, determinism | **ACCEPT** — add the Renew-failure→re-Acquire edge to the state machine before TDD. |
| M2 | **No I/O under the lock.** Decide transition under lock → release → `busctl` off-lock → re-take to commit/Release-stale. Else concurrent first-wave inferences convoy behind a subprocess fork (worst when the daemon is down). | determinism [High], resource-safety M3 | **ACCEPT** — invariant: no subprocess/D-Bus call while the module lock is held. |
| M3 | **Lost-update guard.** The off-lock Acquire opens a window where two threads both act as "the 0→1 caller." Use an **epoch/generation** counter; on commit, store the token only if the epoch still matches, else immediately Release the stale token. Invariant: ≤1 Acquire in flight; every acquired token is stored-or-released. | determinism [High] | **ACCEPT** |
| M4 | **`_enter_lease` is total (never raises); `next_call` invoked exactly once; balanced refcount; idempotent exit** (no `Release(None/0)`, refcount floored at 0). | determinism, security, ai-generation | **ACCEPT** — these become the TDD assertions. |
| M5 | **Renewer owned by the token, not the refcount.** Store `(token, handle, stop_event)` as one unit; at-most-one renewer; it reads the token from shared state under the lock (never a stale closure capture); a cleared token ⇒ stop; a dead renewer while refcount>0 is detected and restarted (or triggers re-Acquire). | determinism, ai-generation, resource-safety | **ACCEPT** |
| M6 | **Assert `next_call` returns a realized response, not a lazy stream.** Verified `chat_completion_helpers.py:1567` returns a materialized `SimpleNamespace`. If a future Hermes returns a generator, the `finally` Release fires *before* tokens generate → guarantee collapses. Add a non-`Iterator`/`Generator` assertion + test. | ai-generation MUST-FIX 1 | **ACCEPT** |
| M7 | **`busctl` hardening ruleset:** `shell=False`, list-form argv, absolute `/usr/bin/busctl`, type-coerce `tier`/`estimate_mib`/`token` before argv, `--timeout=` flag **and** subprocess `timeout=`, cap stdout, strict total reply parsing → fail-open on any anomaly (incl. `free_mib==0` ambiguity), `stdin=DEVNULL`, minimal env (`DBUS_SESSION_BUS_ADDRESS`/`XDG_RUNTIME_DIR` only), never a secret on argv. | security (c) | **ACCEPT** |
| M8 | **Designed-in success observable** (the silent-degradation canary). Fail-open makes a misconfigured `busctl` a no-op indistinguishable from success. Add: (a) a **fail-open counter** + structured logs of every 0→1 Acquire / 1→0 Release / re-Acquire / force-release with token; (b) the E2E proof — one interactive Hermes call during an active dream kills the dream PID (pgrep-gone) and it requeues. | ai-product (missing) | **ACCEPT** |

### Timeout budget (ai-generation), wired into M7
Acquire **150 ms**, Release **150 ms**, Renew **150 ms**, Status **100 ms** (v1.1). Dead
daemon adds **<10 ms** to first token (measured ~3 ms); a *hung* daemon adds **<150 ms** and
never wedges. Any non-`Granted` outcome within the budget → `token=None`, `refcount+=1`,
proceed, **log once** (rate-limited).

## Resolved open decisions (from the brief)
1. **Linger/strobe → DROP from v1.** ai-generation's decisive point: `next_call` blocks
   until the stream is fully consumed, so the lease is held across the **entire generation**
   (30–90 s for a 27B) — *generation latency is the natural anti-strobe dwell*; refcount-0
   gaps appear only in genuine idle. Daemon C7 dwell (8 s) covers batch re-acquire. Revisit
   only on telemetry. (resource-safety wanted a linger; determinism warned it could *worsen*
   batch starvation. Net: defer.)
2. **Cold-start → synchronous Acquire on 0→1; reject optimistic/async.** Latency is noise vs
   seconds-long first token; the async window reintroduces concurrent-batch risk.
3. **Estimate → fixed env value (`AGENTOS_GPU_ESTIMATE_MIB`); NOT model-aware.** Confirmed
   safe: interactive fails open and the preempt branch ignores admission
   (`lease.rs:152-157`), so a wrong interactive estimate can't cause a bad eviction.
4. **Veto → cut** (see Convergence #2).
5. **Priority tag → cut to a commented stub** (see the disagreement section).
6. **Renew cadence → TTL/3**, with the plugin setting `AGENTOSD_LEASE_TTL_SECS`; the
   load-bearing part is M1 (renew-failure→re-Acquire), not the cadence.

## Locked v1 scope
**The `llm_execution` refcounted, self-healing Acquire/Release wrap — alone.** Fail-open,
opt-in (`hermes plugins enable`), per-call-read kill-switch (`AGENTOS_GPU_COORD_DISABLE`),
`busctl` transport behind a `LeaseClient` abstraction (so ADR-0013 A1 swaps in later with no
hook-logic change), token-owned renewer, fixed estimate, fail-open counter + E2E proof.
TDD the pure core (refcount/epoch algebra, reply parsing, self-heal transitions) with a mock
`LeaseClient`; thin impure `busctl` shell — mirroring `lease.rs`'s `admit`/`arbitrate` + thin
shell.

**Cut/deferred:** priority tag (stub), veto (indefinite), linger (telemetry-gated), A1
(no-rework swap later).

## Daemon-side follow-ups (agentosd / ADR-0013 — flagged, not v1 blockers)
- **Enumerable tokens** (security [High]): tokens are monotonic from 1, so a same-uid peer
  can brute-force `Release(t)` to force-release Hermes's lease. Cheap interim mitigation:
  issue `next_token` from a CSPRNG/salted draw (one-line in `install()`). Real fix: A1 peer
  socket + `SO_PEERCRED`. **This plugin only calls Acquire/Release/Status (never Spawn), so
  it does not open the S1 RCE hole** — A1 need NOT be sequenced with the plugin (revises the
  handoff's assumption). Sequence CSPRNG-tokens + A1 as near-term agentosd hardening.
- **Batch `KV_EST × NUM_PARALLEL` under-count** (resource-safety): can still OOM on the
  *batch* side independent of this plugin — ADR-0010 H5 follow-up.

## Strengths (named by ≥3 reviewers)
1. Fail-open correctly scoped to scheduling — a fully-broken plugin can only fail to
   *serialize*, never block or OOM live inference.
2. Refcount-coalescing is the non-obvious correct answer; keeps the fork-heavy `busctl` call
   off the per-call hot path.
3. The `LeaseClient` transport abstraction is the right seam for the A1 swap.
