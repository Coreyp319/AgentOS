# ADR-0022: Bring the Nimbus Blender forge under the lease (creative-app MCP) — Unreal deferred

- Status: **Proposed** (draft — model proposes, code disposes; the human disposes on this). v1 was a
  greenfield design; **v2 (2026-06-17) reconciles it with the EXISTING Nimbus Blender forge** after
  discovering the live setup, and is hardened by a five-reviewer panel. See "Revision history" at end.
- Date: 2026-06-17
- Deciders: pending human + resource-safety-reviewer + security-reviewer + determinism-safety-reviewer
  + responsible-ai-privacy-skeptic + ai-product-reviewer (scope). Hand-offs: wayland-computeruse-reviewer
  (flatpak/portal confinement), reversibility-tx-reviewer (scene-edit rollback).
- Relates to: ADR-0001 (substrate not orchestrator — don't reinvent; **the forge already exists**),
  ADR-0003 (fail-open-supervised — the lease gate MUST fail open so it never breaks the forge),
  ADR-0006/0010 (the VRAM lease), ADR-0011 (overnight batch window), ADR-0013 (owned-spawn /
  allowlisted profiles — **and the reclaim mechanism this ADR must extend for flatpak**), ADR-0009
  (dreaming: shader primary, render/3D as *texture*), ADR-0018 (VRAM coexistence — the budget the lanes
  must share), ADR-0020 (agent-facing GPU MCP — house pattern), ADR-0021 (agent tier-clamp + identity).
- Research input: `docs/research/0012-creative-app-mcp-blender-unreal.md` (2026-06-17).
- External prior art (the Nimbus pack, a separate repo at `~/whitesur-cachyos-pack`):
  `.claude/skills/gpu-effects/blender-mcp.sh` (the lane launcher), `.../reference/blender-pipeline.md`
  (the authoring bible), the repo `.mcp.json` (routes `uvx blender-mcp` per `NIMBUS_BLENDER_PORT`),
  and `~/nimbus_assets/build_*.py` (hero-asset build scripts).

## Context

A working Blender-over-MCP pipeline **already exists** — the Nimbus "forge." Reconciling with it (not
designing greenfield) is the whole point of v2. The live reality:

- **The lane model.** `blender-mcp.sh up` launches **one flatpak Blender per port** (`9876` canonical,
  `9877+` for parallel agents) via `setsid -f flatpak run org.blender.Blender …`, each running the
  ahujasid `blender_mcp_addon` socket; the repo `.mcp.json` routes `uvx blender-mcp` to the same
  `NIMBUS_BLENDER_PORT`. "Parallelized Blender" = **N independent flatpak Blender instances on one
  RTX 4090**, one per agent. This is good isolation for authoring — and a real VRAM problem: the lanes
  are launched **detached and are NOT lease-coordinated**, so N Blenders + ComfyUI + LLM inference
  contend for 24 GB with no admission or eviction.
- **EEVEE-only, flatpak Blender 5.1.2.** The stock flatpak has **only `BLENDER_EEVEE` — no Cycles.** So
  the v1 "unbounded Cycles render, OOM-fragile, heavy lane" framing does **not** apply: EEVEE is a
  real-time rasterizer with a **bounded, much smaller** VRAM footprint. The heavy concern is the *sum*
  of lanes + ComfyUI + inference, not one runaway render.
- **Authoring is code-send.** The forge's entire method is the agent *sending `bpy` code* over the
  bridge (which `exec()`s it in a timer callback); `reference/blender-pipeline.md` is a discipline
  (data-API, idempotent reset-then-build, **verify-by-file**, golden rules) built *around* that. So v1's
  "deny `execute_blender_code`, curated surface only" is at odds with how the forge actually works —
  because the forge is a **trusted, human-operated tool** ("authoring only… never wired into install.sh…
  never installed onto an end user's box"). That is a legitimately different threat model from an
  autonomous or end-user path.
- **The flatpak reparents Blender into a transient systemd scope** (verified: the `bwrap`→`blender`
  tree's top parent is `systemd --user`, in `app-flatpak-org.blender.Blender-*.scope`). Consequence
  that breaks the substrate's core safety property: **the lease's `sigkill_group()` on a launcher's
  process group CANNOT reach a flatpak-scoped Blender.** Reclaim must target the **systemd scope /
  cgroup**, not the process group.
- **The flatpak manifest is wide open:** `filesystems=host`, `shared=network`, `devices=dri`. So flatpak
  is *not* a tight sandbox here — a lane can read/write all of `$HOME` and reach the network freely. Each
  lane's `:9876`-class addon socket is therefore the unauthenticated, host+network-reachable RCE
  primitive the security pass flagged.

So the decision is: **bring the existing lanes under the lease (the substrate's actual job), fix the
reclaim mechanism for flatpak, keep the trusted forge's code-send model, and scope the heavy security
hardening to the autonomous/end-user path that doesn't exist yet.** Unreal stays deferred (research §2).

## Decision

### 1. The forge is the foundation; lanes come under the lease (don't rebuild it)

Keep the lane-per-agent model, the EEVEE flatpak, `blender-mcp.sh`, the `.mcp.json` routing, and the
`blender-pipeline.md` discipline **as-is**. AgentOS's contribution is the one thing the forge lacks and
the substrate exists to provide: **VRAM coordination across the lanes + ComfyUI + inference.** Nothing
about the authoring workflow changes; a lane simply learns to ask the coordinator before it consumes the
GPU, and to be reclaimable when the desktop needs it back.

### 2. Lane VRAM coordination — admit-before-launch (now), evict-via-scope (next)

Two halves, sequenced by risk:

- **Admit-before-launch (the first move; read-only, safe).** `blender-mcp.sh up` consults the
  coordinator before `flatpak run`: read `Status() → (held, tier, token, free_mib)` and refuse to launch
  a lane when an **Interactive** holder is active, or when `free_mib < NIMBUS_BLENDER_EST_MIB +
  headroom`. Because the keyhole/NVML already attributes each running flatpak Blender's VRAM, `free_mib`
  *already reflects sibling lanes* — so each new lane admits against true current headroom. This is
  predict-before-load (ADR-0010) using only the read-only verb, with **no token held** (so no
  peer-disconnect lifecycle problem from a fire-and-forget launcher), and **fail-open**: a coordinator
  that is unreachable lets the lane launch (ADR-0003 — never break the forge).
- **Evict-via-scope (next, the agentosd change).** True coordination needs agentosd to reclaim a lane's
  VRAM when a higher tier preempts. Because flatpak breaks `sigkill_group` (Context), the lease gains a
  **scope/cgroup reclaim path**: a lane registers its systemd scope (resolved from the lane's listening
  PID → `/proc/<pid>/cgroup`) as the token's reclaim handle, and the evictor runs `systemctl --user stop
  <scope>` / writes `cgroup.kill` instead of a group SIGKILL. This is a deliberate, reviewable extension
  to ADR-0013's reclaim mechanism (a new owned-holder *flavor*: "externally-launched, cgroup-reclaimed"),
  and it is gated on the human + resource-safety review because it touches the destructive path.

### 3. EEVEE footprint — a modest fixed per-lane reservation, not the Cycles ceiling

`NIMBUS_BLENDER_EST_MIB` is a fixed conservative const (default ~3000 MiB for an EEVEE authoring lane;
env-tunable), **never an agent input**, pinned by a test. ADR-0018's `coexist` learner still does not
apply (no Ollama telemetry), but the EEVEE footprint is bounded and stable, so a fixed reservation is
honest — unlike the v1 Cycles case, there is no incremental-allocation OOM-mid-render hazard for the
interactive lane. (A future Cycles-enabled *batch render* path — §6 — would reinstate the conservative
ceiling + the deliberate-OOM test.)

### 4. Two-tier threat model — trusted forge keeps code-send; hardening binds the autonomous path

- **Trusted forge (today).** The human-operated authoring lanes keep the `bpy` code-send model — it is
  the point, and `blender-pipeline.md`'s golden rules + **verify-by-file** are the existing discipline
  (the determinism story for a *trusted* operator: the human reads the proof render). No curated-surface
  restriction is imposed here. The residual risks are owned honestly: each lane's `:9876` socket is
  unauthenticated and, under the wide-open flatpak manifest, host+network-reachable.
- **Autonomous / Hermes / end-user path (does not exist yet — gated).** The moment a non-human agent
  drives a lane unattended, or anything ships toward an end-user box, the v1 hardening binds: a
  **by-construction curated allowlist** (no code-execution tool surfaced), the lane socket **bound to
  localhost and severed from host reach**, a **tightened flatpak manifest** (drop `network`; narrow
  `filesystems` from `host` to a work dir), an **egress allowlist** for any cloud-gen tool (default
  absent), version-pinning + tool-description hashing, and the tier-clamp wiring (`CallerClass::Agent`,
  ADR-0021 GO-1 — today `Spawn` hardcodes `Trusted`). This is the line the reviewers drew, placed where
  it actually applies.

### 5. Local-first generation unchanged

If/when the lanes use generative asset tools, local text/image→3D routes through the **existing ComfyUI
lease** (`comfy_client.py`); the ahujasid cloud tools (Rodin/Hunyuan-cloud/Sketchfab/PolyHaven) stay
**absent from any autonomous curated allowlist** (that absence is the tool-poisoning mitigation, not a
runtime scanner), and any opt-in needs informed+revocable consent + an egress allowlist. The trusted
forge, on a human's call, may use them — but note the wide-open flatpak `network` perm means that egress
is currently unconstrained; tightening it is part of §4's autonomous gate.

### 6. Rendered frames feed the dreaming pipeline (EEVEE is fine; a Cycles batch path is separate)

EEVEE renders (the forge's `hero_core.png` turntable path, or a calm abstract still) drop at the lucid
**anchor-frame seam** (`lucid_engine.py --image`), staying inside ADR-0009 (render/3D as *texture*).
EEVEE is well-suited to the calm/abstract dream aesthetic and far lighter on the shared GPU. A
**Cycles-enabled batch render** (needs a different Blender build than the EEVEE flatpak) would be a
separate, lease-owned `Spawn` profile in the heavy lane with the v1 conservative-ceiling + deliberate-OOM
safety test — deferred until there is a demand the EEVEE path can't meet. The forge's **verify-by-file**
rule carries over: the pipeline hook checks the output frame exists with non-zero size, never "render
returned."

### 7. Reversibility & scene state

Scene edits are reversible by the forge's own discipline (idempotent reset-then-build over the bridge;
the agent works in a lane's own scene, never a shared canonical asset). The lease gate is additive and
fail-open: removing it is deleting the opt-in block — the forge reverts to exactly today's behavior.

### 8. Unreal deferred behind a Linux render-feasibility gate

Deferred, and **re-validated 2026-06-17** by a parallel-subagent pass (nothing for Unreal is installed on
this box — confirmed). The gate stays NO-GO on the **durable** reasons: the **Path Tracer (UE's only
film-quality MRQ output) is Windows/DX12-only** — Linux gets the Vulkan rasterizer/Lumen, and Vulkan
path-tracing on Linux is experimental and reportedly fails RT profile checks on NVIDIA; the shared 24 GB
4090 has **~5.8 GB real headroom** (ComfyUI resident), so a UE editor (3–16 GB) would OOM it; and **EEVEE +
ComfyUI/Wan already cover the calm/abstract aesthetic** (§6), so the marginal value doesn't justify the
cost. Two premises updated vs v1: Epic now ships **precompiled Linux binaries** (the "source-build-only"
claim is dropped — install cost is lower but doesn't flip the gate), and **Epic's first-party UE 5.8 MCP
has shipped** (the predicted future base) — but it is **editor-automation only, no render/MRQ tool**, so it
doesn't yet do the thing this pipeline needs. Revisit when a Linux render at acceptable quality fits the
budget, or a Windows render node enters scope; the cheapest re-test is a single headless
`UnrealEditor-Cmd … -RenderOffscreen -MoviePipelineConfig=…` frame (via **runreal/unreal-mcp** — MIT, Linux
binaries, plugin-free, code-send Python → MRQ) watched under `nvidia-smi` against the live ComfyUI headroom.

## Sequencing

- **Phase 0 (the first move — admit-before-launch).** Wire `blender-mcp.sh up` to the read-only `Status`
  gate (§2), **opt-in (`NIMBUS_LEASE=1`) and fail-open**. Immediately stops a new lane from launching into
  insufficient VRAM or onto a busy interactive GPU, coexisting safely with ComfyUI/inference — using only
  existing machinery, killing nothing. Reversible (delete the block).
- **Phase 1 (evict-via-scope).** The agentosd reclaim extension (§2): scope registration + `cgroup.kill`
  /`systemctl --user stop` reclaim for a flatpak lane holder, with the tier-clamp wiring. Gated on human +
  resource-safety review (touches the destructive path); integration-tested deliberately (and **not** by
  killing the user's live authoring lanes).
- **Phase 2 (autonomous gate).** Only if a non-human agent drives lanes unattended: the §4 hardening
  (curated allowlist, localhost-bound socket, tightened flatpak manifest, egress allowlist, version
  pinning). Until then it stays a documented precondition, not built.
- **Deferred.** A Cycles batch-render profile (§6); Unreal (§8).

## Consequences

**Positive.** AgentOS does its actual job — coordinating the shared 4090 — for a real, in-use pipeline,
without rebuilding any of it (ADR-0001). The lanes gain safe coexistence with ComfyUI + inference; the
forge workflow is untouched (fail-open, opt-in). The reclaim-by-scope fix is reusable for any other
flatpak/portal-launched GPU consumer. The threat model is placed where it applies, so the trusted forge
isn't burdened with end-user hardening it doesn't need.

**Negative / risks.** (a) The evict-via-scope reclaim is a new destructive path in the substrate
(cgroup kill of a systemd scope) — must be reviewed and carefully integration-tested, never against live
work. (b) The lanes' `:9876` sockets remain unauthenticated + host+network-reachable under the current
flatpak manifest; this is acceptable for the trusted forge but is the hard precondition (§4) for any
autonomous use, and should be written down as a known exposure, not silently tolerated. (c) Admit-
before-launch is admission-only — it prevents over-subscription at launch but cannot evict a running lane
(that is Phase 1); a lane that was admitted can still be in the way until evict-via-scope lands. (d) The
fixed per-lane EEVEE est is a guess (bounded, but a heavy EEVEE scene with many high-res textures could
exceed it) — acceptable because EEVEE has no incremental-allocation OOM and NVML reads true free VRAM at
the next lane's admission.

**Reversibility.** Phase 0 is a fail-open, opt-in block in `blender-mcp.sh` — delete it and the forge is
exactly as before. Phase 1's reclaim path is additive to agentosd; the lane registration is the only new
state and it is ephemeral. No migration.

## Alternatives considered

- **Keep the forge separate / uncoordinated (status quo).** Rejected by the human's call to unify: N
  uncoordinated Blenders + ComfyUI + inference on one 4090 is exactly the contention the substrate exists
  to arbitrate.
- **Replace the lane model with one shared editor + single lease holder (v1 §5).** Rejected: the
  lane-per-agent model is good authoring isolation and already built; coordinate the *sum*, don't
  collapse it.
- **Own the lane via `Spawn` and `sigkill_group` (the existing reclaim).** Rejected: flatpak reparents
  Blender into a systemd scope, so group-SIGKILL can't reach it — reclaim must be scope/cgroup-based (§2).
- **Hold a cooperative `Acquire` lease from `blender-mcp.sh up`.** Rejected for Phase 0: the fire-and-
  forget launcher exits, and peer-disconnect auto-release (ADR-0013 B4) would free the lease immediately.
  Admission via read-only `Status` avoids the lifecycle trap; holding+evicting is Phase 1's owned model.
- **Deny the `bpy` code-send tool in the forge.** Rejected: it is the forge's method and the operator is
  trusted; the deny/curated surface binds the autonomous path that doesn't exist yet (§4).
- **Adopt Cycles now for renders.** Deferred: the stock flatpak is EEVEE-only and EEVEE suits the calm
  dream aesthetic at far lower VRAM; a Cycles build is a separate batch-render path if ever needed (§6).

## Open questions

1. **Scope reclaim mechanics (Phase 1, gates the destructive path).** `systemctl --user stop <scope>`
   vs writing `cgroup.kill` vs `flatpak kill <instance>` — which most reliably + promptly frees a lane's
   VRAM, and how does agentosd resolve a lane's scope robustly under N parallel lanes? (resource-safety)
2. **Per-lane VRAM estimate.** Is a single `NIMBUS_BLENDER_EST_MIB` const enough across authoring vs a
   texture-heavy EEVEE turntable render, or does it need two profiles?
3. **Autonomous trigger.** What concrete event flips a lane from "trusted forge" to "autonomous"
   (§4's gate) — a Hermes-initiated session? an unattended cron? — and is that detectable at the lease?
4. **Flatpak manifest tightening.** Can the autonomous path run a Blender flatpak override that drops
   `network` and narrows `filesystems` to a work dir without breaking the addon bridge? (security +
   wayland-computeruse-reviewer)
5. **Unreal gate criteria (§8).** Unchanged from v1.

## Revision history

- **v2.3 (2026-06-18).** Unreal MCP **editor-automation lane now LIVE + verified** (the human elected to
  install UE on Linux, lifting the §8 deferral for an experimental lane). Setup: Epic precompiled UE
  **5.8.0** Linux binary at `~/UnrealEngine`; **runreal/unreal-mcp** (MIT, `npx`, Python Remote Execution)
  routed beside `blender` in the forge `.mcp.json`; scaffolding + runbook in `integrations/unreal/`
  (`launch.sh` writes a blank project + RE config + the Linux loopback multicast route, then launches the
  editor). Verified: `editor_run_python` round-trips into the running editor (returned engine version
  `5.8.0-…`). **The §8 render gate is UNCHANGED** — this lane is editor automation only; UE's Path Tracer
  is still Windows/DX12-only, Linux gives rasterizer/Lumen, and neither runreal nor Epic's first-party MCP
  exposes a Movie Render Queue tool (renders go via `editor_run_python` or a CLI `-MoviePipelineConfig`
  job). Still open: the agentosd VRAM-lease profile for the UE lane (design intent in the integration
  README; not a flatpak scope, so a `Spawn`/process-group reclaim, not `AdoptScope`).
- **v2.2 (2026-06-17).** Unreal deferral **re-validated** (parallel-subagent pass) — still NO-GO. Confirmed
  nothing for Unreal is installed on the box. Corrected two stale §8 premises: Epic now ships **precompiled
  Linux binaries** (dropped the "source-build-only" claim), and **Epic's first-party UE 5.8 MCP shipped**
  (editor-automation only — no render/MRQ). Re-anchored the deferral on the durable reasons (no Linux path
  tracer, ~5.8 GB shared-VRAM headroom → OOM, no marginal value over EEVEE + ComfyUI/Wan). Detail in
  `docs/research/0012-…` §2.4.
- **v2.1 (2026-06-17).** Phase 1 (evict-via-scope) **built + reviewed + verified.** The
  `resource-safety-reviewer` + `security-reviewer` returned ITERATE (6 BLOCKING — headline: a shape-only
  `.scope` guard could SIGKILL the user's editor); all findings folded in: a Blender-flatpak **allowlist**
  + daemon-side PID resolution (no caller path), **GO-2** scope-token identity binding (with B4 skipped for
  owned holders so a fire-and-forget launcher can't drop a live lane), **fd-pinning** (TOCTOU), and a
  **measured-free backpressure** on scope preempt. Shipped as `scope_reclaim.rs` + an `AdoptScope` D-Bus
  verb in `lease.rs`; 90 unit tests green + the destructive primitive and full
  adopt→preempt→cgroup.kill→auto-release verified against a THROWAWAY scope (never a live lane). Detail +
  finding-by-finding resolution: `docs/design/0022-blender-lane-scope-reclaim.md`. Remaining for the human
  to dispose: flip `blender-mcp.sh` from the Phase-0 `Status` gate to `AdoptScope` ("Going live").
- **v2 (2026-06-17).** Reconciled to the live Nimbus forge after discovering it: EEVEE-only flatpak (not
  Cycles), the lane-per-agent model (not a single shared editor), code-send authoring as the trusted
  forge's method (so the deny/curated surface moves to a two-tier model binding only the autonomous
  path), flatpak systemd-scope reparenting (so reclaim becomes scope/cgroup-based, not group-SIGKILL),
  and the wide-open flatpak manifest as a named exposure. The decision pivots from "build a hardened
  bridge" to "bring the existing lanes under the lease."
- **v1 (2026-06-17).** Greenfield design (hardened by the five-reviewer panel): adopt Blender MCP,
  curated surface, render as a Cycles `Spawn` profile in the heavy lane, shared HTTP bridge + gateway +
  sandbox, Unreal deferred. The panel's findings (the `:9876` RCE primitive, `Spawn`=`Trusted` today,
  coexist-can't-learn-renders, consent/egress mechanism, gateway+sandbox = net-new) are carried into v2
  where they still apply (the autonomous path, §4) and corrected where the forge reality changed them.
