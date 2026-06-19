# Design 0022 — Blender lane scope/cgroup reclaim (ADR-0022 Phase 1)

Implementation spec for the one new destructive primitive ADR-0022 needs: letting the agentosd lease
**reclaim a flatpak-scoped Blender lane's VRAM** under preemption. This is a NEW destructive path in the
substrate — gated on human + resource-safety + security review.

**Status (2026-06-17): reviewed → hardened → implemented → verified.** The `resource-safety-reviewer`
and `security-reviewer` returned **ITERATE** on the first cut (6 BLOCKING findings between them — the
headline: a shape-only `.scope` guard would let a reclaim SIGKILL the user's *editor*). Every BLOCKING +
CONCERN finding is now folded into the build (`scope_reclaim.rs` + the `AdoptScope` verb in `lease.rs`),
the full suite is green (90 + 1 ignored integration test), and the destructive primitive AND the full
`AdoptScope → preempt → cgroup.kill → auto-release` path are verified against a **throwaway** Blender-named
scope (never a live lane). The one thing left for the **human to dispose**: flipping `blender-mcp.sh` from
the Phase-0 `Status` gate to `AdoptScope` (see "Going live" at the end). See "Review outcomes" below for
the finding-by-finding resolution.

## The problem (why the existing reclaim doesn't reach a lane)

The lease reclaims an agentosd-*spawned* owned job with `sigkill_group(pid)` — a negative-PID SIGKILL of
the process group agentosd put the child in (`process_group(0)`, `lease.rs:307,339`). A Nimbus Blender
**lane** is launched externally by `blender-mcp.sh` (`setsid -f flatpak run …`), and flatpak reparents
it into a **transient systemd user scope** (`app-flatpak-org.blender.Blender-NNNN.scope`, verified:
the `bwrap`→`blender` tree's top parent is `systemd --user`). Those processes are **not** in the
launcher's process group, so a group SIGKILL cannot reach them. The lease cannot own a lane the way it
owns a `Spawn` child.

## The mechanism (cgroup v2 `cgroup.kill`, no root)

cgroup v2 gives the robust per-lane primitive. Each scope has a `cgroup.kill` attribute; writing `"1"`
SIGKILLs the **entire subtree atomically**. Verified on this box: the live lane's
`…/app-flatpak-org.blender.Blender-3630677696.scope/cgroup.kill` is `--w-------` owned by `corey` — i.e.
**writable by the user** (systemd delegates the user's cgroup subtree), so the agentosd `--user` daemon
needs **no privilege**. Natural lane exit is detectable via `cgroup.procs` being empty (or the cgroup
vanishing) → auto-release.

**Safety guard (load-bearing):** the reclaim target is derived from a `/proc/<pid>/cgroup` line and is
**refused unless the leaf unit ends in `.scope`** — never a `.slice`, never the root. This makes it
impossible for a reclaim to target `user.slice` / `-.slice` / `user@1000.service` and take the whole
session down. Path-traversal (`/..`, `//`) is also rejected before joining under `/sys/fs/cgroup`.

## What's implemented (`scope_reclaim.rs` — hardened core, identity not just shape)

The pure parse + guards are unit-tested; the fd-backed IO is integration-tested against a throwaway scope.

- `ScopeHandle { cgroup_path, scope_unit }` + `parse_proc_cgroup` / `parse_scope_path` — the parse + the
  `.scope`-only **shape** guard (rejects slice/service/root/traversal — a reclaim can never hit the
  session tree).
- `LANE_SCOPE_PREFIXES` + `is_lane_scope(unit)` — the **identity** allowlist (B1): only
  `app-flatpak-org.blender.Blender-*.scope` is reclaimable. A fixed, auditable const, never an env knob.
- `resolve_lane_scope(pid)` — the daemon reads `/proc/<pid>/cgroup` **itself** and applies shape +
  allowlist; it never trusts a caller-supplied path (B1).
- `open_scope_dir(handle)` → pins the scope's cgroup **dir-fd** at adopt time; `kill_scope_at(&dir)` /
  `scope_is_empty_at(&dir)` act via `openat` on that fd (B3 — a recycled scope name can't redirect the
  kill; a gone scope → fail-closed). `kill_scope_at` is idempotent (`Ok(false)` on a gone scope);
  emptiness counts only a **gone** scope, never a transient read error (C2).

## The integration (`lease.rs` — implemented)

1. **Holder model.** `OwnedJob` now carries an `enum Reclaim { Spawned { child, pid }, Scope { handle,
   dir, lane_pid } }`. A `Spawn` job is `Spawned` (group SIGKILL + `Child` reap); a lane is `Scope`
   (cgroup.kill via the pinned fd, **no `Child`** — agentosd didn't spawn it).
2. **`AdoptScope(tier, est, lane_pid) → (granted, token, msg)`.** Takes a **PID, not a path** (B1):
   resolves + allowlists the scope server-side, pins the dir-fd, then routes through the *same*
   `do_acquire` core (predict-before-load `admit` + `arbitrate` + the GO-1 clamp) — so admission is
   re-checked under the lock at adoption (C1). Caller class **Trusted** for the human forge; the autonomous
   path is gated (see Review outcomes C2).
3. **GO-2 identity binding (B2).** A scope holder records `holder_peer = Some((token, caller))` (like a
   cooperative holder), so only the adopter may `Release` it — the small monotonic token alone is
   guessable. **But** the supervisor *skips* the B4 peer-disconnect auto-release for any owned holder, so a
   fire-and-forget launcher (`blender-mcp.sh` exits after launch) can't drop a live lane.
4. **Evict dispatch.** The preempt path carries the victim's `Reclaim` **off the lock** (C5) and calls
   `perform_reclaim`: `Spawned` → `sigkill_group` + off-path reap; `Scope` → `reclaim_scope` = cgroup.kill
   via the pinned fd, then **backpressure** the grant — poll the pinned fd until the scope empties
   (bounded), settle, re-read free — so the successor doesn't allocate into not-yet-freed VRAM (B2).
5. **Auto-release.** The supervisor reaps `Spawned` via `try_wait` and `Scope` via `scope_is_empty_at`
   (gone-only = empty, C2), under the lock + by the job's own token (race-safe, C3). The TTL backstop
   still covers a hung lane.
6. **Release semantics.** `Release` of a `Scope` holder **does not kill the lane** (it's the user's
   authoring app) — it drops the lease tracking (the pinned fd closes) and leaves the lane running
   uncoordinated. Only a `Spawned` job is SIGKILLed on Release.
7. **`blender-mcp.sh` (the human's switch — NOT flipped).** Phase 0's `Status` gate becomes an
   `AdoptScope` call *after* the lane is up and its PID is known. Fail-open retained (coordinator
   unreachable / non-lane PID / un-pinnable scope → lane runs uncoordinated, ADR-0003). See "Going live".

## Review outcomes (2026-06-17 — resource-safety + security; both ITERATE → resolved)

Headline: the first cut guarded scope *shape* (`.scope` leaf) but not *identity*. Both reviewers verified
on the live box that `init.scope`, the user's **VS Code** (`app-code-2882.scope`), konsole, and Spotify
all pass a shape-only guard and all have a `corey`-writable `cgroup.kill` — i.e. the mechanism as first
designed could SIGKILL an app the user is working in. (Confirmed again here: this session's own shell runs
in `app-code-2882.scope`.) Resolution, finding by finding:

| Finding | Severity | Resolution |
|---|---|---|
| **B1** guard is shape-only → could kill the editor | BLOCKING | Allowlist (`is_lane_scope`, Blender-flatpak prefix only) + daemon resolves the scope from a **PID**, never a caller path (`resolve_lane_scope`). |
| **B2 (sec)** token unbound → any same-user peer can `Release`/redirect | BLOCKING | Scope holders bind `holder_peer` (GO-2), reusing the cooperative path; supervisor skips B4 disconnect-release for owned holders so the fire-and-forget launcher exiting can't drop the lane. |
| **B2 (res)** `reclaimable=true` credits VRAM before the driver frees it → OOM | BLOCKING | `reclaim_scope` **backpressures** the grant: poll the pinned fd until empty, settle, re-read free before the successor proceeds. |
| **B3 (sec)** TOCTOU — path re-resolved at kill, scope id recyclable | BLOCKING | **fd-pinning**: pin the cgroup dir-fd at adopt; `kill`/`empty` go via `openat` on it; a recycled/gone scope → `ENOENT` → fail-closed. |
| **C1** admit↔adopt race | CONCERN | `AdoptScope` routes through `do_acquire` → live free re-read + `admit` under the lock at adoption. |
| **C2** `scope_is_empty` false-empty on transient error | CONCERN | Only a *gone* scope (`ENOENT`/`ESTALE`/`ENODEV`) counts empty; transient errors read as alive. |
| **C3** natural-exit vs preempt race (no `Child` for scopes) | CONCERN | Supervisor detects scope exit under the lock + releases by the job's own token (monotonic-token no-op on a late detect); `kill_scope_at` idempotent. |
| **C4** reclaim verb choice | CONCERN | `cgroup.kill` is primary (privilege-free, atomic, no user-manager round-trip); write-failure (old kernel) **fails open**, never auto-falls-back to slow `systemctl stop`. |
| **C5** blocking IO under the arbitration lock would wedge the coordinator | CONCERN | All scope IO runs **off** the `Inner` lock (the reclaim handle is carried out, then acted on). |
| **C2 (sec)** `AdoptScope` is destructive — autonomous path must be gated | CONCERN | `CallerClass::Trusted` today; documented hard precondition that the autonomous path (ADR-0022 §4) cannot reach this verb until GO-2 identity + §4 hardening land. |
| Reversibility (handoff) | NOTE | A lane kill is **not** reversible (agentosd didn't spawn it); restore = the agent re-runs `blender-mcp.sh up`. `Release` of a lane is non-destructive (does not kill it). |

## Test + rollout plan (executed)

- **Unit:** `cargo test -p agentosd --bins` — 90 green. Pins the shape guard (slice/root/traversal/non-v2
  → `None`), the lane allowlist (editor/terminal/Spotify rejected, Blender accepted), and `is_gone`'s
  only-gone-is-empty classification.
- **Integration, on a THROWAWAY scope — NEVER a live lane:**
  - `cargo test -p agentosd --bins -- --ignored reclaim_primitive` — spawns
    `systemd-run --user --scope --unit=app-flatpak-org.blender.Blender-test*.scope … sleep`, exercises
    the real `resolve_lane_scope → open_scope_dir → kill_scope_at` path, asserts the scope empties +
    idempotent re-kill. **Passes.**
  - `integrations/blender/test-scope-reclaim.sh` — full `AdoptScope → interactive preempt → cgroup.kill →
    auto-release` against a throwaway Blender-named scope, with the freshly-built daemon on a **private**
    D-Bus + isolated runtime dir (never the live coordinator). **Passes** — and confirms the lane survived
    its adopter's `busctl` disconnect (B4-skip) before being reclaimed by preemption.
- **Rollback:** the verb + holder branch are additive; disabling is reverting `blender-mcp.sh` to the
  Phase 0 `Status` gate. No persistent state.

## Going live (the human's switch)

`blender-mcp.sh` (in `~/whitesur-cachyos-pack`, a separate repo) still uses the Phase-0 read-only `Status`
gate. To arm reclaim, after the lane is up and `port_pid` is resolved, call (fail-open):

```sh
# NIMBUS_LEASE=1 path, after the lane's listening PID is known:
busctl --user -- call org.agentos.Coordinator1 /org/agentos/Coordinator1 \
  org.agentos.Coordinator1 AdoptScope suu batch "${NIMBUS_BLENDER_EST_MIB:-3000}" "$port_pid" \
  || true   # coordinator unreachable / non-lane pid → run uncoordinated (ADR-0003)
```

Hold the returned token for an explicit `Release` when the lane is torn down (optional — natural-exit
auto-release + TTL also cover it). Requires the **rebuilt** daemon to be the one on the bus
(`~/.local/bin/agentosd` re-installed from this build).

## Open questions (follow-ups, not blockers)

1. **Single-lease vs N-lane.** The lease is a single exclusive holder, but the forge runs N parallel
   lanes. `AdoptScope` registers **one** lane at a time (consistent with the daemon); coordinating a *pool*
   of batch lanes (a multi-holder batch tier, or per-lane sub-leases) is a larger design step. Today an
   `AdoptScope` of a second lane while a first holds the batch lease will **queue** — acceptable + safe
   (the second runs uncoordinated, admit-before-launch already bounded its start), but it's the real next
   design question. Until then, document that only one lane is lease-reclaimable at a time.
2. **Per-lane VRAM estimate.** A single conservative `NIMBUS_BLENDER_EST_MIB` (~3000) vs a texture-heavy
   EEVEE turntable needing two profiles (ADR-0022 open-Q2).
3. **Autonomous trigger.** The concrete event that flips a lane "trusted → autonomous" (ADR-0022 §4) and
   whether it's detectable at the lease — the gate for binding `CallerClass::Agent` to `AdoptScope`.
