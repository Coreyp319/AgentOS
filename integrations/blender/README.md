# integrations/blender — creative-app coordination (ADR-0022)

This directory holds the AgentOS-side Blender integration for
[ADR-0022](../../docs/adr/0022-creative-app-mcp-blender-unreal.md). **Read the ADR's revision history
first** — v2 reconciled the plan with the *existing* Nimbus Blender forge, which moved the active work.

## Where the active work lives (not here)

The "parallelized Blender" is the **Nimbus forge lane model** in a separate repo:
`~/whitesur-cachyos-pack/.claude/skills/gpu-effects/`. One flatpak Blender per port (9876, 9877…), each
an `blender_mcp_addon` MCP endpoint, **EEVEE-only flatpak Blender 5.1.2**. The substrate's contribution
(ADR-0022 §2, the chosen first move) is **VRAM coordination of those lanes**, implemented as an opt-in,
fail-open **admit-before-launch** gate in that repo's `blender-mcp.sh`:

```bash
NIMBUS_LEASE=1 .claude/skills/gpu-effects/blender-mcp.sh up
```

It reads the coordinator's `Status` and refuses to launch a lane when the GPU is in interactive use or
there isn't room (predict-before-load; no token held; coordinator-unreachable → launches anyway,
ADR-0003). Evicting a *running* lane under preemption — the agentosd scope/cgroup-reclaim change needed
because flatpak reparents Blender into a systemd scope (so `sigkill_group` can't reach it) — is **ADR-0022
Phase 1**: now **built + reviewed + verified** (`AdoptScope` D-Bus verb +
`crates/agentosd/src/scope_reclaim.rs`). Flipping `blender-mcp.sh` from the `Status` gate to `AdoptScope`
is the human's remaining step — see "Going live" in `docs/design/0022-blender-lane-scope-reclaim.md`.

### Verifying the reclaim (throwaway scope, never a live lane)

`test-scope-reclaim.sh` drives the full `AdoptScope → interactive preempt → cgroup.kill → auto-release`
against a disposable Blender-named systemd scope wrapping `sleep`, with the freshly-built daemon on a
**private** D-Bus (never the live coordinator):

```bash
cargo build -p agentosd
integrations/blender/test-scope-reclaim.sh        # → PASS
cargo test -p agentosd --bins -- --ignored reclaim_primitive   # the destructive primitive in isolation
```

## What's in this directory: the DEFERRED Cycles batch-render path (ADR-0022 §6)

`render.py`, `render-wrapper.sh`, and `phase0-render.sh` are a hardened skeleton for a **future**,
lease-owned, headless **Cycles** batch render that could feed the dreaming pipeline a heavy frame the
interactive EEVEE lanes can't produce. **They are deferred and NOT runnable on this box as-is:**

- They assume **Cycles + OptiX**, but the stock flatpak Blender is **EEVEE-only** — a Cycles-enabled
  Blender build is a prerequisite. EEVEE renders (lighter, suit the calm dream aesthetic) come from the
  forge lanes instead (ADR-0022 §6).
- They assume a plain `blender` PATH binary and `sigkill_group` reclaim; the real env is flatpak +
  systemd-scope (see Phase 1 above).
- `crates/agentosd/src/lease.rs` registers a `blender-render` Spawn profile pointing at
  `render-wrapper.sh` — allowlisted but **unused** until this §6 path is taken.

What they *do* encode (and why they're kept): the design the ADR's autonomous/batch path requires — a
**fixed, repo-owned render script** (never an agent param), **scalar-only validated params** with
work-dir path containment, `--factory-startup --disable-autoexec` (no `.blend` autoexec RCE), a Cycles
VRAM cap so a heavy scene fails its own frame, and the **deliberate-OOM acceptance test**
(`AOS_BLENDER_STRESS`) that must prove the desktop survives + the lease reclaims VRAM before the flat
"cannot wedge the desktop" claim is trusted.

## Status summary

| Piece | State |
|---|---|
| Lane admit-before-launch | **Done** — `blender-mcp.sh` (Nimbus pack), opt-in `NIMBUS_LEASE=1`, fail-open, logic-tested |
| Lane evict-via-scope (cgroup reclaim) | ADR-0022 Phase 1 — **built + reviewed + verified** (`AdoptScope` + `scope_reclaim.rs`); human flips `blender-mcp.sh` to go live |
| Cycles batch-render (`render.py`/wrappers here) | ADR-0022 §6 — **deferred**, needs a Cycles Blender build; not runnable on the EEVEE flatpak |
| Autonomous hardening (curated surface, tightened flatpak manifest, egress) | ADR-0022 §4/Phase 2 — gated, only if a non-human agent drives lanes unattended |
| Unreal | ADR-0022 §8 — deferred behind the Linux render-feasibility gate |
