# Design-council brief — ADR-0023 Phase-B proactive UE-throttle controller

- Status: **PAUSED by Corey's surface decision (2026-06-19) — see the banner below. Was:
  Proposed (Phase-B architecture; gated on the remaining Phase-A measurements).**
- Date: 2026-06-19
- Facilitator: design-discourse-mediator (neutral; reconciles + decides, does not generate)
- Relates to: [ADR-0023](../adr/0023-creative-environment-pipeline.md) (creative-environment
  pipeline — amendment target), [ADR-0001](../adr/0001-substrate-not-orchestrator.md)
  (substrate not orchestrator / don't reinvent), [ADR-0003](../adr/0003-fail-open-supervised.md)
  (fail-open supervised), [ADR-0004](../adr/0004-graphics-yield-kill-relaunch.md) (graphics
  yield = kill/relaunch, not live shedding), [ADR-0005] (apply/rollback / reversible-by-default),
  [ADR-0009] (shader-primary — **inverted** by ADR-0023), [ADR-0010](../adr/0010-vram-coordinator-overnight-batch-lane.md)
  (predict-before-load admission + one exclusive lease), [ADR-0013](../adr/0013-coordinator-ipc-trust-and-lease-lifecycle.md)
  (IPC trust / lease lifecycle), [ADR-0018](../adr/0018-vram-coexistence-budget-partition.md)
  (coexistence partition + actuator pattern), [ADR-0022](../adr/0022-creative-app-mcp-blender-unreal.md)
  (creative-app MCP; UE deferral lifted for editor automation only).
- Inputs reconciled: four candidate architectures (A · Lease-proactive step-down; B ·
  Pressure-reactive AIMD governor; C · Frame-budget/GPU-time governor; D · Hybrid
  predict-then-track + kill-not-degrade safety net) and their adversarial verdicts across
  four lenses (`resource-safety`, `determinism-safety`, `rust-feasibility`, `ambient-calm`).
- Artifacts proposed by this brief: this brief; the ADR-0023 amendment stub (§10).

> **PAUSED (Corey's decision, 2026-06-19).** The §12.5 surface BLOCK was taken to a focused
> fork-resolution (`0023-phaseb-surface-strategy.md`). The council recommended **Option C**
> (UE-as-texture → the in-process `aurora` shader), which would have **retired** R1–R5, the
> ADR-0005 source-swap tx, the fsync blocker, and G5. Corey instead governed the vision call to
> **Option A** — the live, authoritative UE stage — which is verified-infeasible-as-a-buildable-
> option today. **Consequence: no resident UE wallpaper ships near-term, so this controller is
> held**; the procedural `aurora` shader remains the wallpaper. The surface doc's §7 amendments
> are **not** applied; the §12.5 BLOCK and §12.7 verdict-1 **stand**. Revisit when A's
> native-Wayland boot-survival probe passes (see the surface doc's "Human decision of record").

---

## 0. The one-paragraph decision

**Adopt approach D — "Bracket": a yielding-resident UE lease tier whose `Preempt` is a
non-destructive `Throttle` first-attempt, with the lease's existing own-PID SIGKILL +
relaunch-to-shader as the guaranteed floor** — because it is the only candidate that resolves
the central CRUX honestly (it throttles live as an *optimisation that can never block the
kill floor* the substrate already trusts), scored highest across every lens (avg 6.0, with
the only `rust-feasibility` 7 in the field), and reuses the proven `fits_after_evict` /
`reclaim_scope` backpressure / `wind.rs` lock-isolation patterns rather than inventing a new
concurrency model. We graft three corrections from the runners-up: **(C/B) the two-axis
GPU-time-vs-VRAM rung model** so the design is honest that on today's scene the lever is
util, not capacity; **(all-four ambient verdicts) an eased rung *transition* and an authored
FLOOR "mood,"** because every candidate's instant-cvar step was the single worst calm
violation; and **(every lens) the confirm-off-the-acquire-handler fix** so a throttle wait can
never stall human inference. **This brief is a proposal-of-a-proposal: the controller is
unbuilt and is gated on four still-PENDING Phase-A measurements (§8). Code and the human
dispose.**

---

## 1. The measured ground (Phase-A, `spikes/ue-probe/`)

Phase-A measured a packaged UE 5.8 `-game` build (Lumen, Vulkan, `-RenderOffscreen`, 1440p)
on the shared RTX 4090. The headline that drives the whole architecture:

> **On the current (primitive) scene the throttle's real lever is GPU-TIME (util), not VRAM.**
> FULL→FLOOR frees only **~250 MB** VRAM (per-process 1201→970 MiB,
> `spikes/ue-probe/vram_FULL.csv` / `vram_FLOOR.csv`) while whole-card GPU util collapses
> **95.7% → 39.1%** (`README.md:63,66`). The ~1 GB base runtime dominates VRAM and does not
> shrink; only `sg.GlobalIlluminationQuality 0` (Lumen GI off) + `sg.ReflectionQuality 0` +
> `r.Streaming.PoolSize 512` shave the small remainder.

Measured numbers the design is sized against:

| Quantity | Value | Source |
|---|---|---|
| FULL per-process UE VRAM | 1187–1201 MiB (card-delta ~1.3 GB) | `vram_FULL.csv`, `README.md:63` |
| FLOOR per-process UE VRAM | 970–980 MiB (card-delta 1000–1041 MiB, agrees ≤3%) | `vram_FLOOR.csv`, `README.md:65` |
| **VRAM freed FULL→FLOOR** | **~250 MB** | `README.md:67` |
| **GPU util FULL→FLOOR** | **95.7% → 39.1%** (the load-bearing yield) | `vram_FULL.csv`/`vram_FLOOR.csv` util col |
| Pool-cap actuation latency | **~1.40 s** parse→physical 512 MB resize | `ue_measure_FLOOR.log:1255→1346` |
| Map load (boot) | ~38–42 ms, no crash | `ue_measure_*.log:1245` |
| Packaging | BuildCookRun ~47 s, 914 MB staged | `README.md:60` |
| Editor cold-Lumen spike (avoid as co-tenant) | ~22 GB | `README.md:58` |
| Card total | 24564 MiB; ~7.7 GB graphics/Plasma baseline | `telemetry.rs:97`, ADR-0018 §1 |

**The richer-scene caveat (load-bearing, repeated in every §):** every VRAM number above is
on a tiny primitive scaffold (ground/cubes/spheres). A representative dark-ride tableau
(textures/Nanite/more Lumen surfaces) **will** grow the ~1 GB base, and the FLOOR rung's
Lumen-off + pool-cap will then yield *meaningfully more* VRAM — possibly flipping the
GPU-time-vs-VRAM balance toward capacity (`README.md:70-72`). The architecture below is
identical under both regimes (same ladder, same admission math); **only the two measured
footprint numbers change**, which is exactly why the re-measure is a hard gate (§8).

---

## 2. The four approaches considered

Scores are the adversarial-lens averages from the council; all four were verdict **iterate**.

| # | Approach | res-safety | det-safety | rust-feas | ambient | avg | Why-not (one line) |
|---|---|---|---|---|---|---|---|
| A | Lease-proactive step-down | 6 | 5 | 6 | 4 | **5.3** | "Throttle before spawn" head-start is illusory — the child spawns under the held `Inner` lock, so the off-lock PUT *races* the spawn it must precede; trigger-vs-lock unsound as drawn. |
| B | Pressure-reactive AIMD governor | 6 | 6 | 5 | 3 | **5.0** | Two writers (admission + AIMD) to one rung with no arbiter; the reactive loop is the weakest calm story (rung strobe) and lowest feasibility (blocking settle-wait + async-HTTP gap). |
| C | Frame-budget / GPU-time governor | 5 | 6 | 5 | 4 | **5.0** | Its spine — UE *keeps* the lease while a heavier gen *also* runs — is a **two-holder phantom**; `LeaseState` has one `holder: Option<Held>`, so this is a multi-holder rewrite, not an extension. |
| **D** | **Hybrid predict-then-track + kill safety net** | **6** | **6** | **7** | **5** | **6.0** | **Winner.** Throttle is a non-destructive first attempt that always falls *through* to the proven kill; the only candidate whose core composes with the single-exclusive lease unchanged. |

**The best ideas we graft onto D (the mediator's job — reconcile, don't flatten):**
- From **C/B**: the explicit **two-axis budget** (GPU-time-share *decoupled from* VRAM-floor),
  so the design never pretends a `t.MaxFPS`/`ScreenPercentage` rung freed capacity it didn't.
  This is the single most honest reading of the Phase-A headline and D's `gpu_time_lever`
  already half-states it — we promote it to first-class.
- From **all four ambient verdicts** (the rare unanimous finding): the instant-cvar step is
  the worst calm violation in every design. We graft an **eased UE-internal rung *transition*
  + an authored FLOOR mood + a stale/blind cockpit tell** (§6) as binding, not polish.
- From **every lens's blocker on D**: **move the bounded confirm OFF the acquire handler**
  (queue-and-retry, supervisor-owned), so a throttle wait can never serialize a human/Hermes
  Interactive grant (§3, §6).

---

## 3. The recommended architecture in depth (D · "Bracket", corrected)

### 3.1 The CRUX, resolved explicitly

The substrate is **kill-not-yield** (the only trusted reclaim is own-PID SIGKILL or
`cgroup.kill`; `POST /free` measured 0 MiB; SIGSTOP frees 0 — ADR-0010 §5, ADR-0004 Spike #2)
and **UE crashes-not-degrades** under Vulkan VRAM pressure. The product needs the *opposite*:
a persistent wallpaper that **yields-and-restores, never dies** (kill = black wallpaper =
forbidden, ADR-0023). These collide head-on.

**Bracket resolves it by layering, with one non-negotiable rule: the LEASE owns the kill; the
throttle governor can only *ask* UE to shrink — it can never authorize a grant and never veto
a kill.**

- **Layer 1 — proactive admission (the spine).** UE registers as a lease holder at a NEW tier
  `Yielding=1` (below `Batch`) carrying **two** footprints, `est_full_mib` and
  `est_floor_mib`. When a heavier `Acquire`/`Spawn` arrives, `arbitrate` returns a NEW
  `LeaseDecision::Throttle{ to_floor }` (between `Queue` and `Preempt`) instead of `Preempt`.
  The pure twin `fits_after_throttle(free, est_full, est_floor, succ_est, headroom)` —
  modelled exactly on `fits_after_evict` (`crates/agentosd/src/lease.rs:419-422`) — predicts
  post-throttle free as `free + (est_full - est_floor)`.
- **Layer 2 — reactive trim (optimisation only).** A `wind.rs`-shaped lock-free governor on a
  slow ~2 s tick reads live `util_pct`/`free_mib` (already sampled, `telemetry.rs:172-179`)
  and selects the *smallest sufficient* rung *within the envelope admission already
  authorised* — it tightens, never loosens past the admitted floor, and **never grants or
  kills**. This keeps it legal under ADR-0018 amendment item 1's ban on a tick-by-tick
  resident-set optimiser: declarative desired-state → pure diff → one greedy idempotent action.
- **Layer 3 — the kill floor (unchanged, proven).** If throttle fails to actually shed (Lumen
  only frees on map-reload, RC dropped the PUT, UE is mid-hang) or even FLOOR + the gen
  overruns usable VRAM (the Wan-14B ~17 GB cliff), control falls **through** to the existing
  `Reclaim::Spawned` process-group SIGKILL (`lease.rs:401-406`) + supervisor relaunch to the
  **shader fallback floor** (ADR-0009 inverted) via an **ADR-0005 apply/rollback source-swap
  tx**. A crash is therefore never the *mechanism* — a deliberate, reversible, tx-gated
  kill-and-degrade-to-shader is.

So: **throttle-live is the optimisation bolted on top; degrade-by-kill is the floor; the
optimisation layer is structurally incapable of blocking the floor.** That is the only framing
that survives "UE crashes-not-degrades on a kill-not-yield substrate."

### 3.2 The exact trigger

**Primary trigger = the lease arbitration event** (not telemetry pressure). The throttle fires
when a heavy `Acquire(tier≥Batch)`/`Spawn` is arbitrated against the `Yielding` UE holder —
which is *before the gen allocates a byte*. This is mandatory: a reactive throttle that fires
*after* pressure appears races the ~1.4 s actuation against a CUDA OOM and loses, and UE dies
rather than degrades. **Secondary trigger = the slow governor tick** (telemetry): fine-tune the
rung within the authorised envelope and **restore UE upward, edge-driven off the lease Release
event** (not a transient free-VRAM rise). Telemetry NEVER triggers a kill.

### 3.3 The grafted corrections (resolving the council's blockers)

These are the deltas every lens demanded; without them D does not exceed ~6.

1. **Confirm off the acquire handler (the unanimous determinism/feasibility/res-safety
   blocker).** The original "block the grant on a bounded NVML confirm" puts a ~2 s await on
   the hot path of every higher-tier `Acquire` — which would stall human/Hermes Interactive
   inference (an ADR-0003 / ADR-0010 R2 fail-open *inversion*). **Corrected:** on `Throttle`,
   under `Inner` set `desired = FLOOR` + commit an intermediate `Holder::ThrottlePending`
   state, **drop the lock**, and return **`queued`** to the heavy caller (reusing the existing
   no-wait-queue retry contract, `lease.rs:30-32`). The **supervisor poll loop** (already
   750 ms, `lease.rs:961-1046`) owns the bounded confirm off-lock — exactly the
   `reclaim_scope` backpressure shape (`lease.rs:807-824`): on a later tick, if a fresh NVML
   read shows free rose by the predicted delta → complete the grant; if the deadline passed →
   fall through to SIGKILL + relaunch-to-shader, then grant. **Inference never blocks on UE
   actuation; it retries against already-reclaimed VRAM.**
2. **Two-axis rung model (grafted from C/B).** Each rung is tagged with *which* resource it
   yields. GPU-TIME levers (`r.ScreenPercentage`, `t.MaxFPS`, `sg.ShadowQuality`,
   `sg.PostProcessQuality`) yield contention/occupancy only; VRAM levers
   (`sg.GlobalIlluminationQuality 0`, `sg.ReflectionQuality 0`, `r.Streaming.PoolSize 512` +
   `LimitPoolSizeToVRAM 1`) yield capacity (`spikes/ue-probe/cvar_ladder.md:84-94`). Admission
   picks `YIELD_TIME`→REDUCED vs `YIELD_VRAM`→FLOOR deterministically from *which axis the
   incoming job pressures*. On today's scene the VRAM delta is ~250 MB so REDUCED (time yield)
   is the everyday state and the credited VRAM delta is **near-zero by default**; on a richer
   tableau the same code credits whatever the re-measure shows.
3. **Single-writer desired-rung + eased transition (grafted from the unanimous ambient
   verdict).** `desired_rung` is a single-writer field: only admission/lease-events lower the
   floor-clamp; the governor may select within `[clamp, FULL]` but can never raise above the
   clamp while a heavy holder is pending (kills the B/D two-writer race). And — separately from
   the substrate decision — **the UE consumer eases between rungs** over a calm time-constant
   (critically-damped, README slow-bridge ω ~1–1.5, *not* the wind drag-ω ~7): crossfade
   `ScreenPercentage`, ramp Lumen contribution, ease `MaxFPS`, dim-then-cut the binary Lumen
   toggles. **Rung changes must never be a single-frame cliff** (§6).

---

## 4. How it composes with the existing substrate (reuse vs net-new; ADR-0001)

**Honoring "don't reinvent" (ADR-0001/0002): the controller is a *client* of the one lease, not
a second daemon, not a second VRAM accountant, not a new orchestrator.**

**REUSE (verbatim or extended):**
- **Pure admission/arbitration core** — `admit`, `arbitrate`, `Tier`, `fits_after_evict`
  (`coord.rs:145-175`, `lease.rs:419-422`): extended with one tier (`Yielding`), one decision
  variant (`Throttle`), one pure twin (`fits_after_throttle`). Same unit-test discipline.
- **`reclaim_scope` backpressure-the-grant loop** (`lease.rs:807-824`) — the exact precedent
  for "act off-lock, then a bounded NVML-confirm poll holds the response, fail-open on
  timeout." The corrected confirm (§3.3.1) is this shape with the terminal kill swapped for a
  non-terminal RC PUT and a fall-through to the kill.
- **`wind.rs` sink pattern** (`crates/agentosd/src/wind.rs:14-27,48,191-195`) — the explicit
  precedent for an agentosd→engine live signal (the window-drag wind producer → `Wind1` sink →
  `wind.json` → UE consumes it like the shader did). The throttle is its **reverse-direction
  sibling** (coordinator→engine vs desktop→engine). We copy it byte-for-byte: a third object
  path `org.agentos.Throttle1` mounted via an `attach()` twin on the **one** lease zbus
  connection (beside `Coordinator1`/`Wind1` at `lease.rs:1067-1072`), its **own**
  `Arc<Mutex<ThrottleState>>` with **no path to `lease::Inner`**, pinned by a
  `throttle_path_takes_no_inner_lock` test. **This is the load-bearing safety property** (the
  RC PUT can never delay a SIGKILL) and it is enforceable by the compiler + a tripwire test,
  not by discipline.
- **ADR-0018 actuator pattern** — declarative desired-state → pure diff → one greedy gated
  idempotent action per tick → anti-strobe dwell. The governor *is* this, applied to rung
  selection; it is **not** the forbidden free-running PID and **not** a second scalar
  accountant (admission stays one scalar live-NVML read).
- **`Reclaim::Spawned` SIGKILL + Spawn PROFILES allowlist** (`lease.rs:76-101,401-406`) — UE is
  a NEW hardened `ue-wallpaper` Spawn profile (absolute `AgentOSBlank.sh` wrapper, no caller
  argv, ADR-0013 A2), **not** `AdoptScope` (UE is not a flatpak — confirmed by ADR-0022 v2.3 /
  `integrations/unreal/README.md:78-92`). agentosd owns the PID, so the kill floor is free.
- **Anti-strobe preempt dwell + TTL + supervisor** (`lease.rs:119-136,961-1046`) — debounce,
  the confirm-poll owner, and crash-relaunch.
- **`free_mib()` spawn_blocking NVML + own-handle-per-mode** (`coord.rs:290-300`) — both the
  confirm and the governor reuse this; never a shared handle.
- **`lease.json` mirror + `keyhole` + `mcp.rs` perceive tools** (`keyhole.rs:127-134`,
  `mcp.rs:69-103`) — extended with a read-only throttle rung/reason field for the cockpit; no
  new server, no act verb (stays on the trusted session bus; ADR-0021 GO-2 not built).

**Relationship to ADR-0018 AIMD partition + AdoptScope:** the throttle does **not** add an AIMD
loop — that was approach B's weakness and is explicitly *out*. The warm-pool/heavy-lane
partition (`analyze.rs`) and the scalar admit remain the one accountant; UE's `est_floor_mib`
is simply a budget term subtracted from the scalar budget when a heavy tier is pending.
AdoptScope's cgroup machinery is reused only conceptually (fd-pinned, allowlisted) — UE uses
the *Spawn* path, not the scope path.

**NET-NEW (the genuinely missing levers):**
1. `Tier::Yielding` + `LeaseDecision::Throttle{to_floor}` + `fits_after_throttle` — the first
   **non-terminal reclaim** the substrate has ever had (every existing lever is SIGKILL/
   `cgroup.kill`).
2. The two-number-footprint yielding-resident holder representation.
3. `org.agentos.Throttle1` + the governor tick + the **async** RC HTTP client (see §5 — this
   is net-new, *not* a reuse: the crate only has `reqwest::blocking`).
4. The crash-relaunch-to-last-rung supervisor for the `ue-wallpaper` PID (flagged unbuilt in
   the grounding).
5. The KDE Application-Wallpaper Wayland surface wiring (separate KWin spike) + the ADR-0005
   UE↔shader source-swap tx.

---

## 5. Actuation path + the latency unknown

**Channel = UE Remote Control HTTP over loopback** (the Phase-A-verified path,
`spikes/ue-probe/remote_control_setup.md:136-147`):

```
PUT http://127.0.0.1:30010/remote/object/call
{ objectPath: "/Script/Engine.Default__KismetSystemLibrary",
  functionName: "ExecuteConsoleCommand",
  parameters: { WorldContextObject: null, Command: "<rung string>" },
  generateTransaction: false }
```
HTTP 200 + empty/`{}` body = accepted. Liveness probe = `Command:"stat fps"` (no side effect).
Server opened at launch via `-ExecCmds='WebControl.StartServer, <initial rung>'` +
`-RCWebControlEnable` belt-and-braces; bind **127.0.0.1 only** via `[HTTPServer.Listeners]
DefaultBindAddress=127.0.0.1` in `DefaultEngine.ini` (RC is unauthenticated — loopback is
mandatory). The Python Remote Execution channel (`:6766`) is **rejected** — discovery fails
headless `-RenderOffscreen`. **`-ExecCmds` quoting: NO inner quotes** (UE re-quotes; collision
idles the engine forever).

**The rungs** (single comma-separated `Command` strings, drop-in from
`spikes/ue-probe/cvar_ladder.md`, tagged by axis):

| Rung | cvar string | Yields |
|---|---|---|
| **FULL** | `r.ScreenPercentage 100, sg.ViewDistanceQuality 3, sg.AntiAliasingQuality 3, sg.ShadowQuality 3, sg.GlobalIlluminationQuality 3, sg.ReflectionQuality 3, sg.PostProcessQuality 3, sg.TextureQuality 3, sg.EffectsQuality 3, sg.FoliageQuality 3, sg.ShadingQuality 3` | nothing (native) |
| **REDUCED** (`YIELD_TIME`) | `r.ScreenPercentage 70, sg.GlobalIlluminationQuality 2, sg.ShadowQuality 2, sg.ReflectionQuality 2, t.MaxFPS 30` | **GPU-time** (Lumen stays on; ~250 MB only) |
| **FLOOR** (`YIELD_VRAM`) | `r.ScreenPercentage 50, sg.GlobalIlluminationQuality 0, sg.ReflectionQuality 0, sg.ShadowQuality 0, sg.PostProcessQuality 0, r.Streaming.PoolSize 512, r.Streaming.LimitPoolSizeToVRAM 1, t.MaxFPS 5` | **GPU-time + VRAM** (Lumen GI+Refl off, pool capped) |
| SHADER-FALLBACK | *not a cvar* — ADR-0005 source-swap tx → SIGKILL UE → procedural shader | full ~1 GB+ (the cliff exit) |

**Rung strings are a fixed daemon-owned allowlist** (ADR-0013): the caller names a tier/intent,
never a console command — no arbitrary `ExecuteConsoleCommand` injection.

**The actuation-LATENCY unknown — handled three ways, measured first:**
- **TOLERATE.** The throttle is never on the OOM-prevention critical path: the supervisor-owned
  bounded confirm admits the heavy job only against VRAM **actually re-read as free**, never
  against a promise. However long the PUT truly takes, a slow/dropped throttle costs a *kill*,
  never an OOM.
- **PRE-EMPT.** `-ExecCmds` carries the *initial* rung, so UE **boots at FLOOR** if the GPU is
  already busy (safe-by-default) — latency only ever has to cover REDUCED→FLOOR, not
  FULL→FLOOR.
- **MEASURE (v0, gating).** Wire `spikes/ue-probe/measure_packaged.sh`'s 1 Hz sampler to
  timestamp `PUT ExecuteConsoleCommand(FLOOR)` against the `nvidia-smi` free/util settle — the
  proposed-but-unwired Phase-A method — to replace the assumed ~2 s confirm deadline with a
  measured constant, and to answer **whether a live cvar reclaims already-resident Lumen VRAM
  or only caps future growth** (if map-reload-only, the VRAM axis collapses to "kill+relaunch
  at a seam" and only the GPU-time axis is a live lever).

**Async-HTTP feasibility note (the `rust-feasibility` catch):** the crate's `reqwest` is
`features=["blocking","json"]` only (`Cargo.toml:10`); there is **no async HTTP client** in the
daemon today. The RC client is therefore **net-new**, not a reuse. Decision: wrap a bounded-
timeout `reqwest::blocking` PUT in `tokio::task::spawn_blocking` (mirroring `free_mib`'s
NVML pattern, `coord.rs:290-300`) with a single-flight guard, OR enable the async feature
behind a build flag so `monitor`/`feed` keep the minimal dep tree. The governor tick uses
`tokio::time::interval` (skips, never accumulates) and supersedes an in-flight PUT at the next
tick (idempotent rung → last-write-wins is safe).

---

## 6. Failure modes, fail-open (ADR-0003), and the crash-not-degrade safety net

**Tier asymmetry is binding (ADR-0010 R2):** Interactive fails **OPEN** (grants on unreadable
NVML); `Yielding`/`Batch` fail **CLOSED**. A controller bug must degrade to "UE
unthrottled-or-killed," **never** to "inference blocked" or "desktop frozen."

| Failure | Behaviour | Why safe |
|---|---|---|
| RC server never opened (`:30010` flakiness) | Liveness `stat fps` fails → UE pinned at launch-time FLOOR; confirm times out → fall through to SIGKILL+relaunch-to-shader | Inference never blocks; desktop never wedges; UE loses only the *optimisation*, degrades to the proven kill floor |
| Actuation latency > gen ramp / Lumen frees only on map-reload | Confirm deadline expires → Preempt (SIGKILL) → relaunch boots at FLOOR | Gen admitted only against **reclaimed** VRAM; slow throttle costs a kill, never an OOM |
| Governor tick stalls/panics | UE holds last rung; lease path wholly independent (own mutex, no `Inner` reach) | `wind.rs` structural guarantee — can never delay a SIGKILL |
| agentosd crashes entirely | systemd `Restart=always`; supervisor re-adopts UE PID, re-reads last rung, re-probes RC | Fail-open to "UE runs, maybe full quality," never "inference dark" |
| Heavy gen exceeds even FLOOR+budget (Wan-14B cliff) | Admission DENIES coexist → ADR-0005 source-swap tx to shader **commits BEFORE** SIGKILL → gen runs against shader desktop; UE relaunches on Release | No window of "UE dead and shader not up" = no black wallpaper |
| **Un-leased GPU squat** (the off-lease ComfyUI hole already observed) | **Outside the safety envelope** — proactive sequencing cannot be enforced for a job that allocates without acquiring | Mitigated only by UE's safe-by-default FLOOR resting footprint + the kill backstop; **named, not silently assumed** (see §8) |
| Bad cvar wedges UE | Watchdog kill-on-no-progress + relaunch; rungs are a fixed allowlist | Only cache/quality mutated — nothing un-rollback-able (ADR-0010 §2) |

**Crash-not-degrade safety net (three guards):** (1) **safe-by-default footprint** — UE's
resting rung whenever any heavy job is resident is FLOOR, set at *launch*, so a missed throttle
tick is survivable; (2) the **bounded confirm** is the wedge-detector — it never optimistically
grants against un-shed VRAM; (3) the **supervisor owns the PID** (an 8 h VRAM zombie already
happened) — kill-on-no-progress via `stat fps` + UE-log `device-lost|VK_ERROR|out of memory`
scan, relaunch crash-loop-bounded with backoff (a perpetually-crashing cook degrades to the
shader, never a relaunch storm).

**The cooperative-holder gap (`resource-safety` blocker, recorded honestly):** Hermes inference
acquires via the **cooperative** `Acquire` (agentosd owns no PID; `lease.rs:833-843`). The
"UE sheds, THEN gen allocates" sequence only holds if the cooperative caller waits for
`granted` before allocating. The **cooperative-revoke signal is unbuilt** (`lease.rs:31-32`).
The design must (and this brief does) state that proactive throttle protects only jobs that
route through the lease and allocate strictly after grant; the un-leased squat is a known,
separately-tracked hole, not a covered case.

**Ambient/calm safety (the unanimous verdict — these are blockers, not polish):**
- **Eased transition, not a step.** One cvar PUT snapping FULL→FLOOR (Lumen GI+Refl off,
  ScreenPercentage 100→50, 30→5 fps) is the textbook attention magnet that ADR-0023's own
  consequences say must "revert to neutral." The UE consumer **interpolates** toward the target
  rung over the calm slow-bridge ω; binary Lumen toggles dim-then-cut or ride a uDreamMix-style
  crossfade.
- **FLOOR is an authored "the ride rests" mood, not raw `sg.* 0` ugliness.** At deep yield the
  dark-ride **camera freezes** (a still reads calm; a 5 fps panning camera reads as a *hung
  process*), and the look is dimmed/cooled by design so FLOOR reads **calmer** than FULL.
- **A first-class stale/blind tell.** When RC is unreachable the stage must NOT keep showing a
  smooth full-Lumen lie. The keyhole cockpit gets a `<1 s`-legible "stage is yielding to
  `<holder>`" cue (reuse the existing keyhole RT-yield "sunrise sweep" grammar), with an
  mtime/heartbeat on `throttle.json` so "blind" is visually distinct from "idle." Never let
  blind == serene. (Contrast/non-color-redundancy of the cue → `ui-accessibility-reviewer`.)
- **reduce-motion fallback.** The primary throttle lever (FPS + ScreenPercentage) *is*
  motion-rate and resolution — exactly what reduced-motion must remove. A non-motion encoding
  of "busy" (a static dim/tint step within calm limits) is required so the grammar survives.

---

## 7. Determinism & reversibility (ADR-0001 model-proposes/code-disposes)

**Model proposes, code disposes — no model output reaches any grant/deny/throttle/kill
decision.** Every decision is pure deterministic math on a fresh NVML read:

- **Deterministic + validated:** `admit`, `arbitrate`, `fits_after_throttle`, and the
  governor's rung-selection diff are pure functions with unit tests (the impure shell only does
  I/O: NVML read, RC PUT, file write). Given `(free_mib, util_pct, holder, request)` the rung
  and the decision are fully determined. Rung selection is the ADR-0018 declarative-desired-
  state → pure-diff → one-greedy-gated-action shape (gated by the scalar `admit`), never a PID.
- **Single-writer / idempotent restore.** `desired_rung` has one authority: admission lowers
  the floor-clamp; the governor selects within it. **Restore is edge-driven off the lease
  Release events** (`lease.rs:989,1027,1044`) and is recomputed as a **total function of
  current holder state** — applying the same release twice is a no-op; a release-races-new-admit
  interleave re-clamps to FLOOR (new admit wins). Pin with a `restore_is_idempotent` test (the
  way `wind.rs` pins `idle_frame_is_byte_stable`).
- **Reversible.** A throttle cvar only **caps/reduces** quality (Lumen scene cache, texture
  pool, resolution, FPS) — it mutates no desktop/system state and is fully reversed by sending
  FULL (ADR-0010 §2/§5: preemption may destroy only a cache artifact). **The throttle adds
  ZERO new irreversible acts.** The one irreversible act (SIGKILL of the owned UE PID) is
  unchanged and still priority+fit-gated; the one desktop-state mutation (UE↔shader source
  swap) routes the ADR-0005 apply/rollback tx (uDreamMix crossfade) and is itself
  idempotent/replayable (a double-fired kill must not double-apply the crossfade).
- **Idle byte-identical.** With no heavy holder the governor sends nothing and UE sits at FULL;
  `throttle.json` is edge-written and byte-stable at rest — the calm/idle-drift tripwire.
- **Replay harness (required, matching house discipline).** A `--once`-style one-shot that,
  given a synthetic `(holder, request, free_mib, util_pct, rc_landed?)` snapshot, emits the
  rung + admit verdict with no live UE/NVML/RC — so the rung mapping, the no-credit-until-
  verified rule, the idempotent restore, and the over-budget→kill escalation each get a pinned
  golden, exactly like `admit`/`arbitrate` today.
- **Enum-renumber audit (correctness, not style).** Inserting `Yielding=1` below `Batch` shifts
  every `derive(Ord)` discriminant, the `clamp_agent` ceiling (`coord.rs:82-84`), the
  `from_arg`/`as_str` maps, and every persisted/mirrored tier integer. Pin the full four-tier
  priority lattice with a test; assert an unreadable-NVML `Yielding` acquire fails **CLOSED**
  (only Interactive fails open); assert no `match` on `Tier` absorbs the new variant via a
  wildcard.

---

## 8. Open questions + the measurement gates (this brief is GATED on them)

**This is a proposal-of-a-proposal.** The verified crate today is a read-only monitor/producer
+ the built lease/coordinator; the UE throttle tier, the governor, the RC client, the
crash-relaunch supervisor, and the wallpaper surface are all **unbuilt**. Before any lease-core
surgery, four still-PENDING Phase-A probes must clear — they are HARD gates, not risk bullets:

| Gate | Question | Why it gates | Probe |
|---|---|---|---|
| **G1 — richer-scene re-measure** | Does a representative dark-ride tableau make FLOOR yield meaningfully more than ~250 MB VRAM? Does per-process still track card-delta on a texture-heavy scene (gotcha #7)? | The whole two-number footprint / VRAM-admission value rests on this; until it lands, the controller **credits ZERO VRAM** (GPU-time yield only) | re-author tableau via `author_scene.sh` → `measure_packaged.sh` (size `est_floor` from **card-used-delta**, not per-process) |
| **G2 — live-RC actuation latency** | PUT→footprint-change end-to-end (only the ~1.4 s *launch-time* pool-cap is known); and **does a live cvar reclaim resident Lumen, or only cap future growth / free on map-reload?** | Sets the confirm deadline; decides whether the VRAM axis is a live lever at all | `measure_packaged.sh` sampler: timestamp `PUT(FLOOR)` vs `nvidia-smi` settle |
| **G3 — live coexistence interference** | FLOOR-UE + Wan-14B + Ollama simultaneously — which tenant OOMs first, at what total? | Capacity-fit is necessary but mutual non-interference is currently **assumed**; the `coexist_runbook.md` heavy case has **never been run live** | run `coexist_runbook.md` / `coexist_safe.sh` heavy case (currently a manual, un-executed template) |
| **G4 — frame-time CSV under contention** | Real ms/frame at each rung (only fps *targets* 5/30 exist; the CSV profiler produced no output) | The calm/perceptual transition cost and the "FLOOR reads alive not hung" judgement need measured frame-time | fix `measure_frametime.sh` CSV path (`Saved/Profiling/CSV/` did not exist) |

Other open questions (named, not decided-for here):
- **RC auto-start on a real packaged cook** — does `:30010` open via auto-start, or only via
  `-ExecCmds=WebControl.StartServer` / `-RCWebControlEnable`? Unresolved on a real cook.
- **UE-as-Wayland-wallpaper surface** — packaged `-game` + KDE Application-Wallpaper plugin +
  GPU-selection under KWin compositing for an hours-long run. Orthogonal but gates the product;
  a controller with no resident UE to drive is inert. → **`wayland-computeruse-reviewer`**.
- **The ADR-0005 source-swap tx itself is unbuilt** — the kill path's reversible degrade target
  must exist and be proven to survive a Wayland/KWin compositor under a concurrent 17 GB
  allocation **before** any UE kill path is wired. → **`reversibility-tx-reviewer`** +
  **`wayland-computeruse-reviewer`**.
- **Cooperative-revoke signal** (`lease.rs:31-32`) — unbuilt; without it agentosd cannot tell a
  cooperative UE/Hermes holder to throttle.

**Missing voices (named, not filled — a synthesis that silently fills an empty lane is the most
dangerous false consensus):** the four lenses scored were `resource-safety`,
`determinism-safety`, `rust-feasibility`, `ambient-calm`. **`ui-accessibility-reviewer`** must
ratify the non-color/contrast cockpit tell and the reduce-motion encoding; **`sound-designer`**
must be consulted on any throttle/relaunch audio (a chime must never land on a VRAM-yield
flicker); **`responsible-ai-privacy-skeptic`** on the desktop→engine signal posture (geometry/
pressure only, never window content). The throttle controller has **no audio voice present** —
do not decide it here.

---

## 9. The v0 slice (smallest thing that proves the loop) + deliberate deferrals

**v0 ships the kill floor first and lights up live-throttle only after G1+G2 confirm a live
cvar reclaims resident VRAM.** The kill floor is already proven; the throttle is the unproven
part.

**v0 builds (in order):**
1. **The measurement gates G1–G4** as the literal first deliverable (they decide whether the
   tier is worth building). Output lands in `telemetry.jsonl` via the existing sampler.
2. The **`ue-wallpaper` Spawn profile** (hardened absolute wrapper, `-ExecCmds` initial rung +
   `WebControl.StartServer`, loopback bind) + a **supervised host** (systemd `--user`, like
   feed/keyhole/lease) that owns the PID and relaunches-to-last-rung.
3. `Tier::Yielding` + `LeaseDecision::Throttle` + `fits_after_throttle` (pure, unit-tested) +
   the **supervisor-owned bounded confirm** that falls through to the existing
   `Reclaim::Spawned` kill. **This is the whole CRUX loop:** admit-against-throttled-floor →
   confirm-or-kill → relaunch.
4. `org.agentos.Throttle1` + the governor tick (single-flight RC client, `throttle.json`
   mirror, `throttle_path_takes_no_inner_lock` test).

**v0 deliberately DEFERS:**
- The **eased rung transition + authored FLOOR mood** (the UE-consumer-side calm work) — v0
  proves the *substrate loop* with stepped cvars behind a flag; the calm transition is a
  fast-follow that **must land before any user-facing demo** (it is a blocker for ship, not for
  the loop proof).
- The **ADR-0005 UE↔shader source-swap tx** — until it exists and is reviewed, v0's cliff exit
  is the honest ADR-0004 ~800 ms kill/relaunch-to-shader **flicker**, not a crossfade. Do not
  claim "crossfade" until it is built and its visible cost is measured.
- The **keyhole read→control back-channel** — v0 surfaces throttle rung/reason read-only in
  `lease.json`; the cockpit control half is downstream.
- The **richer-scene VRAM credit** — until G1 lands, admission credits zero VRAM delta and the
  ADR states plainly that coexistence with a VRAM-cliff gen (Wan-14B) is **kill-to-shader, not
  throttle**.

---

## 10. ADR-0023 amendment stub (Proposed — gated)

> **ADR-0023 amendment — Phase-B yielding-resident UE throttle tier ("Bracket")**
> - Status: **Proposed** — blocked on Phase-A gates G1 (richer-scene re-measure), G2 (live-RC
>   actuation latency + live-vs-map-reload Lumen reclaim), G3 (live coexistence interference),
>   G4 (frame-time CSV); and on a `reversibility-tx-reviewer` sign-off of the ADR-0005
>   source-swap and a `wayland-computeruse-reviewer` sign-off of the UE-as-wallpaper surface.
> - Change: introduce `Tier::Yielding` (below `Batch`) + a non-terminal
>   `LeaseDecision::Throttle{to_floor}` (between `Queue` and `Preempt`) + a pure
>   `fits_after_throttle(free, est_full, est_floor, succ_est, headroom)` twin of
>   `fits_after_evict`. The UE wallpaper is a `ue-wallpaper` Spawn profile (agentosd owns the
>   PID; **not** AdoptScope — UE is not a flatpak) carrying a **two-number footprint**.
> - Trigger: **proactive at the lease arbitration event** (heavy `Acquire`/`Spawn` vs the
>   `Yielding` holder), throttling UE down the cvar ladder over Remote Control (`:30010`)
>   **before** the gen allocates. A slow `wind.rs`-shaped governor (`org.agentos.Throttle1`,
>   own mutex, no `Inner` lock) fine-tunes the rung within the admitted envelope and restores
>   edge-driven off Release. Telemetry never triggers a kill.
> - The layering invariant (non-negotiable): **the lease owns the kill; the governor can only
>   ask UE to shrink — it can never authorize a grant nor veto a kill.** Throttle is a
>   non-destructive first attempt; on confirm-timeout it falls **through** to the existing
>   own-PID SIGKILL + relaunch-to-shader-floor (ADR-0009 inverted) via an ADR-0005 apply/
>   rollback source-swap tx. A crash is never the mechanism; a deliberate, reversible,
>   tx-gated kill-and-degrade-to-shader is.
> - The one gating safety metric: *a throttle must never delay a human/Hermes Interactive
>   grant, never cause the gen or wallpaper to OOM, and never leave a state where UE is killed
>   but neither relaunched nor the shader floor is showing.* The bounded confirm runs on the
>   supervisor poll loop OFF the acquire handler (queue-and-retry), never blocking inference.
> - Reversibility: a throttle cvar caps/reduces only render quality (a cache artifact, ADR-0010
>   §2/§5) — zero new irreversible acts; restore is a total function of holder state
>   (idempotent). Idle stays byte-identical.
> - Calm clause: rung transitions are **eased** on the UE consumer (slow-bridge ω, not a
>   single-frame cvar cliff); FLOOR is an authored "the ride rests" mood (camera freeze, dimmed/
>   cooled), not raw `sg.* 0` degradation; a stale/blind RC channel is visually distinct from
>   idle on the keyhole cockpit. Ratify with `ui-accessibility-reviewer`; consult
>   `sound-designer` (no chime on a yield flicker) and `responsible-ai-privacy-skeptic`
>   (geometry/pressure-only desktop→engine signal).
> - ADR-0010 §2 is amended to acknowledge a LIVE daytime wallpaper co-tenant (the old "plays
>   from overnight cache by day" assumption no longer holds).

The full ADR text is for code + the human to dispose; this brief proposes, it does not ratify.

---

## 11. Recorded dissent + accepted tradeoffs

**Recorded dissent (never erased):**
- The **`ambient-calm` lens scored D a 5** — the lowest non-zero of D's lenses — and would
  block ship until the eased transition + authored FLOOR mood + stale/blind tell land. We
  fold its deltas in as **binding** (§6) rather than overriding the lane; v0 may prove the
  *loop* with stepped cvars behind a flag, but the calm work is a hard precondition for any
  user-facing demo. The calm lane is not satisfied by "ship the cvars, ease later."
- Across A/B/C, every lens's **blocker was the same family** (the throttle wait stalling
  inference, the unmeasured VRAM credit, the multi-holder rewrite). D wins because it alone
  composes with the single-exclusive lease — but the council did **not** rate D a clean pass;
  it rated it `iterate`. This brief is the iteration, not a ratification.

**Accepted tradeoffs:**
- On today's scene the throttle yields **GPU-time, not VRAM** (~250 MB) — we accept that
  coexistence with a true VRAM-cliff gen (Wan-14B) is **kill-to-shader, not live throttle**,
  until G1 says otherwise. The product claim "UE coexists with Wan-14B" may resolve to
  "degrades to shader for the heaviest gens." We state this honestly rather than overclaim.
- The **un-leased GPU squat is outside the safety envelope** — proactive sequencing cannot be
  enforced for a job that allocates without acquiring. We accept the FLOOR resting footprint +
  kill backstop as the only mitigation until the cooperative-revoke signal is built.
- We **ship the kill floor before the live-throttle optimisation** — the differentiator
  (live yield) earns its place only after G1+G2; the substrate stays honest and safe meanwhile.

---

## 12. Missing-voices review (the five unfilled lanes)

§8 named five lanes as missing-not-filled (`ui-accessibility-reviewer`, `sound-designer`,
`responsible-ai-privacy-skeptic`, `reversibility-tx-reviewer`, `wayland-computeruse-reviewer`).
All five have now reviewed design D ("Bracket"); their verdicts are folded below as **binding**.
The headline: one lane (**wayland-computeruse**) returns **BLOCK** on the resident-surface
foundation, and **all five gate user-facing ship** — the substrate-loop proof (v0 stepped cvars
behind a flag, no kill) is the only thing that proceeds unblocked.

### Consolidated verdict table

| Lane | Score | Verdict | Gates ship? | What it gates |
|---|---|---|---|---|
| ui-accessibility | 5/10 | amend | **yes** | the four §6 a11y clauses (non-color reduce-motion encoding, SR-exposed blind tell, FLOOR-frame contrast, bounded flash rate) — under-specified, not a block |
| sound | 7/10 | amend | **yes** | a chime on the yield/relaunch flicker, and any sound-only reduce-motion backfill — both user-facing-ship blockers; the deliverable is the *absence* of sound, made binding |
| privacy | 6/10 | amend | **yes** | identity discipline on `throttle.json` + the lease `reason` field, file mode/retention/deletion, and a named consent moment for the resident UE |
| reversibility-tx | 5/10 | amend | **yes** | the UE-kill path — the source-swap tx is **unbuilt**; R1–R5 round-trip/crash/idempotency proofs must be green before any kill is wired |
| wayland-computeruse | 4/10 | **BLOCK** | **yes** | the resident wallpaper surface itself — no proven mechanism mounts UE on a Plasma 6 background layer; the controller is **inert** until G-WAYLAND-SURFACE clears |

---

### 12.1 ui-accessibility (amend · 5/10 · gates ship)

**Ratifies:** §6 correctly names the load-bearing hazard most throttle designs miss — the PRIMARY
levers (`t.MaxFPS` 30/5, `ScreenPercentage` 70/50, camera-freeze) ARE motion-rate + resolution,
i.e. exactly what `prefers-reduced-motion` must suppress, so the reduce-motion fallback cannot be
"just don't animate the throttle." It ratifies the §3.3 item 3 / §6 critically-damped eased rung
transition (slow-bridge ω ~1–1.5, not the wind drag-ω ~7) as the right WCAG 2.3.3 time-constant,
the §6 "never let blind == serene" mtime/heartbeat instinct (mirroring the keyhole
effectiveState UNKNOWN-wins-over-stale contract, `KeyholeModel.qml:60-65`), and the authored
camera-freeze FLOOR mood (a slow pan reads as a hung process; a freeze reads calm).

**Binding required additions:**
- **AMEND §6 (reduce-motion):** specify the non-motion busy encoding concretely and make it
  non-color-redundant: (a) reduce-motion forces UE to the camera-frozen / `MaxFPS`-capped look as
  an *independent floor-of-motion regardless of throttle rung* (an eased transition does not
  satisfy reduce-motion — the motion lever itself must be removed); (b) the substitute "busy"
  signal must carry a non-color channel, reusing the cockpit's proven SHAPE-glyph + plain-word
  label redundancy (`KeyholeModel.qml:70-91`), NOT a tint/dim step alone (a WCAG 1.4.1 failure);
  (c) state the dim/tint step's contrast budget so it holds ≥3:1 as a UI mark (WCAG 1.4.11).
- **AMEND §6 + §10 calm clause (the binding gap this lane owns — the SR path):** the §6 stale/blind
  tell is specified *entirely visually* ("sunrise sweep"), and the keyhole has **no live-region
  today** (`README.md:249` flags the "aria-live-on-transitions-only canon … not yet exercised";
  `Accessible.name` is static per-element, `StateToken.qml:41-42`, `FullRepresentation.qml:141-142`).
  A blind SR user who cannot see the sweep keeps hearing the last-good "Lease interactive (Hermes)"
  name — the exact "blind == serene" failure, reproduced in the SR channel (WCAG 4.1.3, 1.4.1).
  Throttle-onset / FLOOR / blind / restore MUST emit a screen-reader status message
  (QAccessible state-change / polite live region; assertive for blind/snag), keyed off the SAME
  derived field as the visual cue (single source of truth, mirroring effectiveState).
- **"Reuse the sunrise sweep" is a DESIGN CLAIM, not a reuse — relabel it `design:/missing:`.**
  The schema-3 keyhole contract (`keyhole.rs:86-109`) has no throttle/yielding/rung field; the
  HorizonStrip 2500 ms sunrise tweens MOOD COLOR (`HorizonStrip.qml:25-29`), not "yielding to
  holder X." The cue must be BUILT: a new honest contract field (throttle rung + holder in
  `keyhole.json` or a sibling `throttle.json`, schema-bumped + pinned like `SCHEMA` in
  `keyhole.rs:55-61`, with mtime/heartbeat so blind ≠ idle) + a distinct SHAPE glyph + plain-word
  label, legible <1 s, AA-verified against the worst-case frame.
- **AMEND §6 (FLOOR mood) + add a contrast clause to §8/§10:** require AA verification of EVERY
  overlaid surface (notification toasts, the keyhole cockpit, panel tray glyphs) against the
  DIMMED/COOLED FLOOR frame, not the FULL idle baseline (4.5:1 text / 3:1 UI). This is the same
  class already documented for the warm-bloom case (`InstrumentPalette.qml:35-40`, warm `#FF9957`
  fails AA at 3.07–3.94:1) — extend it to the new FLOOR frame; build-PR screenshots must include
  the FLOOR frame, not only FULL.
- **AMEND §3.3 item 3 + extend G4 (flash gate):** bound the LUMINANCE-change rate of the eased
  transition, not just its motion smoothness. The dim-then-cut of binary Lumen toggles and the
  GI-off luma swing must be rate-limited so no large-area flash crosses WCAG 2.3.1 (3 flashes/sec,
  general + red-flash). G4 measures frame-TIME, not luma — extend it to per-frame luma delta across
  REDUCED↔FLOOR and FULL↔FLOOR, including when a transition coincides with another reactive bloom.
- **AMEND §9 (deferrals):** the eased transition, authored FLOOR mood, stale/blind tell, and
  reduce-motion encoding are **ship-gating for this lane** — consistent with the §11 dissent that
  the sibling `ambient-calm` lane blocks ship on the same work. v0 may prove the substrate loop
  with stepped cvars behind a flag, but **no user-facing demo ships** until the four §6 a11y
  additions are built and verified.
- **AMEND §6 + §10:** bind any interactive cockpit yield affordance to logical units /
  `Kirigami.Units` with a 24×24 CSS-px minimum target (WCAG 2.5.8) and a SHAPE-based focus ring,
  reusing `FullRepresentation.qml:476-483`, so it survives HiDPI and rides legibly over the warm/dim wash.

**Blocker:** none (verdict amend) — but the single most load-bearing gap is that **every cue in
§6 is described visually and the keyhole has no live-region**, so "never let blind == serene"
currently holds only for sighted users. **Hand-offs:** the GRAMMAR of the "yielding to `<holder>`"
cue and the FLOOR mood as an ambient signal are `ambient-embodiment-reviewer`'s (state the cue's
grammar once there); this lane owns the non-color redundancy + contrast *requirement*. AT-SPI
bbox/coordinate-scale + HiDPI target geometry for the UE-as-wallpaper surface →
`wayland-computeruse-reviewer`. The flash-rate gate is the visual sibling of `sound-designer`'s
no-chime-on-a-flicker rule.

### 12.2 sound (amend · 7/10 · gates ship)

**Ratifies:** §8/§10 correctly **refuse to silently fill the audio lane** — naming `sound-designer`
as missing and deferring rather than inventing an earcon. It ratifies that §6/§10's visual-calm
work (eased transition, authored FLOOR "the ride rests" mood, first-class stale/blind tell) is
exactly what makes silence viable (a visually calm yield needs no audio crutch), that the §3.1
layering invariant means the throttle/kill path has no structural connection to the feed's
earcon-bearing states, and that §9 honestly keeps the cliff exit as the bare ADR-0004 ~800 ms
kill/relaunch flicker (the very flicker a chime must never land on).

**Binding required additions** — the lane's answer is **decided silence, not an open consult**,
and the brief must cite the existing shipped posture as binding:
- **§6 + §10 calm clause MUST state as BINDING:** "A throttle/yield rung-change and the
  kill→relaunch-to-shader cliff are SILENT — no earcon, no swaync toast — by the surface-labor
  contract (`integrations/design/surface-labor.md:8-9,16`), a resource/health transition being a
  visual-only redundant cue, exactly as the keyhole attention ember (no earcon/no toast,
  ADR-0012 §7 / `keyhole-condensed-row-earned-motion.md:200-201`) and unlike the feed's gated
  `needs_you`." Replace §10's open phrase "consult `sound-designer` (no chime on a yield flicker)"
  with this decided posture — **the consult is now answered.**
- **§6 MUST add the structural prevention (not a debounce):** "The kill/relaunch and governor
  paths have NO producer into the audio earcon layer (reserved for the feed's gated
  `needs_you`/`snag` edges, `feed.rs:99-110`); and while busy is high or a VRAM yield is in flight,
  any standing notification earcon is SUPPRESSED — the suppression predicate reads the SAME
  `state`/`busy` field the wallpaper consumes, never a new NVML/RC tap and never the governor
  tick" (else the suppression window and the actual yield window drift and a startle earcon slips
  through during a model swap). A throttle never crosses the `needs_you` (state==2, gateway-gated)
  or `snag` (state==4) edge audio is reserved for — without stating this, a future contributor
  could wire a "yield" chime believing it a new legitimate event.
- **§6 MUST close the reduce-motion redundancy trap (co-owned with `ui-accessibility-reviewer`):**
  because the primary throttle lever IS motion-rate + resolution, reduce-motion removes the
  primary visual yield cue; its replacement is the §6 static dim/tint step (visual, non-motion) —
  **never an audio cue**. AgentOS is never sound-only; a yield must remain legible to deaf/HoH and
  muted users. This lane adds only the "no audio backfill" constraint and defers the visual
  encoding to ui-accessibility.
- **§6 or §10 MUST scope haptics out explicitly:** no haptic channel applies — a desktop Plasma 6
  box has no haptic actuator and a continuous background throttle is not a discrete event; stating
  it prevents a phantom-haptics proposal later.
- **§10 amendment stub** upgrades its sound bullet to the decided clause (silent throttle/yield +
  kill/relaunch; audio fenced to gated `needs_you` and suppressed while busy is high; reduce-motion
  backfilled visually never sonically; no haptics — *ratified by `sound-designer`*).
- **§9 deferral list MUST add a sound line:** the silent-yield + audio-suppression posture is
  **binding from v0** — there is nothing to build (silence is zero-footprint), but the controller
  MUST NOT grow any audio side-effect; a yield/relaunch that makes a sound is a ship blocker, not
  a polish item.

**Blocker:** none (verdict amend) — but the lane **gates ship**: a chime on the yield flicker is
the explicit §8 anti-pattern, and a sound-only reduce-motion fallback would break the
never-sound-only floor; both are user-facing-ship blockers even though the correct deliverable is
the *absence* of sound. Delta-to-10 (3 pts): cite the existing posture as binding, specify the
structural+suppression mechanism, close the reduce-motion trap + scope haptics out.

### 12.3 privacy / responsible-ai-privacy-skeptic (amend · 6/10 · gates ship)

**Ratifies:** §7 reversibility is genuinely strong from a data POV (a throttle cvar caps only
render quality — a cache artifact — mutates no system state, adds zero irreversible acts, records
operations not inferred preferences); the §3.1/§4 structural lock-isolation (own
`Arc<Mutex<ThrottleState>>`, no path to `lease::Inner`, pinned by `throttle_path_takes_no_inner_lock`,
modelled on `wind.rs:18-27,372-392`) is the correct compiler-enforced shape; and §5's actuation
channel is correctly pinned loopback-only (`127.0.0.1:30010`, `DefaultBindAddress=127.0.0.1`,
Python `:6766` rejected, daemon-owned rung allowlist) — local-first holds for this lane.

**Binding required additions** — the central gap is that the brief **borrowed `wind.rs`'s
identity-free safety CLAIM without its identity-free PAYLOAD**:
- **AMEND §4 and §8 — STOP citing `wind.rs` as proof the signal is "geometry/pressure-only, never
  window content."** Verified: `wind.rs`'s entire payload is three floats + a bool
  (`wind.rs:107-108,221`), identity structurally absent. But the throttle is NOT identity-free:
  §6:352-353's "stage is yielding to `<holder>`" cue and §4:226 / §9:465's "reason" field carry
  app/agent identity. The claim is true ONLY for the coordinator→engine RC PUT (allowlisted rung
  strings); it is FALSE for the engine→cockpit `throttle.json` and the `lease.json` reason field.
  Replace with: the RC payload is rung-string-only; the cockpit mirror carries a sanitized holder
  LABEL (the `lease.rs:313-332` basename→friendly-name map, never raw argv/cmdline/task title) and
  a fixed enumerated reason CODE, never free-form narration.
- **AMEND §4/§6 — BIND `throttle.json` to the runtime-dir privacy posture:** it lands in
  `$XDG_RUNTIME_DIR/nimbus-aurora` (0700 dir, tmpfs, per-user, logout-ephemeral — same as
  `lease.json`/`keyhole.json`), NEVER `$XDG_STATE_HOME` and NEVER `/tmp`/world-readable, inheriting
  the atomic temp+rename + edge-write discipline of `write_lease_mirror` (`lease.rs:357-364`) so it
  is byte-stable at idle (no persistent diff churn that re-identifies activity timing). The brief
  currently says nothing about file mode/location/retention — an implementer could put it anywhere.
- **AMEND §6 — CAP the reason/holder field to a CLOSED enum, not a string:** show the sanitized
  holder LABEL (comfyui/blender-lane/ollama — the `short_label` codomain) + a reason from a FIXED
  set {throttling, killed, restoring, blind}; forbid plumbing `inner.last_preempt` narration
  (`lease.rs:283-285`) or any heavy-job argv/task title. Pin with a `holder_field_is_label_allowlist_only`
  test (the `wind.rs` no-identity-on-the-wire shape).
- **AMEND §9 / `restore.sh` (deletion gap — a v0 deliverable, not a deferral):** `restore.sh`
  currently deletes `agent.json`/`keyhole.json`/`lease.json` but NOT a new `throttle.json`, and the
  new `ue-wallpaper` host + throttle/governor are NOT in the UNITS array — "forget this"/clean
  uninstall break the moment this ships. ADD `throttle.json` to the rm list, ADD the new units, and
  confirm `--purge` reaches them.
- **AMEND §8 (the telemetry egress map — currently unmapped):** `telemetry.rs:26,105,457` already
  appends `lease:{tier,holder,preempt}` to the PERSISTENT `telemetry.jsonl` every 2 s. Adding
  `Tier::Yielding` + a Throttle decision means a 7-day timestamped "at 14:32 the wallpaper yielded
  to `<heavy-gen-job>`" record — a behavioral trace of WHEN the user runs heavy creative gens.
  Require the same 0600/0700 + 7-day retention + disclosure-line treatment (`telemetry.rs:429-436`),
  update the disclosure text to name the new yield events, and **prefer recording only the TIER
  transition, not the holder name**, in the persistent log unless the holder is needed for tuning.
- **AMEND §9 — NAME the consent moment for the resident UE wallpaper:** a launch-at-FLOOR resident
  creative engine is an ambient disclosure to anyone viewing the screen ("my desktop is running a
  creative engine right now"); **installing a systemd unit is not informed consent.** Require an
  explicit user enable step (and documented disable) distinct from the agentosd install, plus a
  one-line disclosure of what the resident UE exposes to an over-the-shoulder/screen-share observer.

**Blocker:** none (verdict amend) — none of the additions are architectural; they are
payload-discipline + lifecycle bindings the brief left implicit. The v0 substrate loop can be
proven with a label-only, enum-reason, runtime-dir-0700, edge-written `throttle.json` and no
persistent holder string. **Hand-offs:** loopback RC bind hardening + the Spawn-profile
allowlist/argv sanitization + the async RC client mechanics → `security-reviewer` (this lane rules
only that no identity leaves the box); the resident-UE surface + screen-capture mechanics →
`wayland-computeruse-reviewer` (this lane retains the consent ruling on the resident process as a
screen-visible disclosure); the contrast/non-color encoding of the cue → `ui-accessibility-reviewer`;
the ADR-0005 tx carries no inferred-preference "why" (clean) → `reversibility-tx-reviewer`.

### 12.4 reversibility-tx (amend · 5/10 · gates ship — on an UNBUILT precondition)

**Ratifies:** the brief correctly gates the whole UE-kill path on a `reversibility-tx-reviewer`
sign-off before any kill is wired (§8 G-list, §10 stub) and **honestly flags the source-swap tx as
UNBUILT** rather than pretending the reversible degrade target exists; §7 correctly names the
idempotency hazard ("a double-fired kill must not double-apply the crossfade"), treats restore as a
total function of holder state, and isolates the ONE desktop-state mutation (UE↔shader source swap)
as the only act needing a real tx (the throttle ladder adds zero irreversible acts — right scoping).

**Binding required additions** (the central §6/§3.1 safety claim — "source-swap tx commits BEFORE
SIGKILL → no black wallpaper" — is **currently unsound as written**):
- **AMEND §6 (row "Heavy gen exceeds even FLOOR+budget") + §3.1 Layer-3 — replace "tx commits
  before SIGKILL" with verified-up ordering.** "Tx commit" (an atomic ledger append) is NOT
  "shader surface visibly up" (a multi-step Plasma/KWin reconfigure — plugin-id change + surface
  attach + first frame composited). Binding sequence: (1) tx begin + capture prior state; (2) bring
  up the shader floor surface and BLOCK on a positive "first shader frame composited" ack (not
  merely "plugin set"/"command sent"); (3) fsync the ledger record; (4) THEN process-group SIGKILL
  UE. If the ack cannot be obtained, the tx ABORTS and UE is NOT killed (**fail to keep-UE, never
  fail to black**).
- **AMEND §7 + §10 reversibility clause — add a crash-atomicity spec (ADR-0005 leaves it open).**
  There are **ZERO fsync/sync_all calls in the daemon**; the one "atomic publish" (`wind.rs:191-194`
  temp+`fs::rename`, no file/dir fsync) survives a clean exit but **NOT a power-loss/hard reboot**
  (rename can be reordered before data hits the platter), so a torn backup under a "swapped"
  ledger entry makes a later revert reconstruct the WRONG prior wallpaper. The swap ledger/backup
  MUST be temp → fsync(file) → fsync(dir) → rename → fsync(dir); a ledger entry must never claim
  "swapped" before the backup is durable.
- **AMEND §6/§7 — capture PRIOR STATE, not the action.** §6 captures "relaunch UE" but the swap
  destroyed a UE process AND swapped a Wayland surface. The tx must record the exact rung/argv/scene
  UE was running + the prior wallpaper-plugin config + foreground surface, so "relaunch on Release"
  restores UE to the rung it was at — **not unconditionally FLOOR per §5 PRE-EMPT** (else the revert
  silently lands in a different rung = lossy capture = not a true revert). Extend §7's replay harness
  to a swap apply→revert round-trip asserting wallpaper source + plugin config + UE rung bit-identical
  to pre-swap, including a crash injected between mutate and ledger-append.
- **AMEND §7 — mechanize the idempotent swap (currently only asserted).** The swap-to-shader and
  swap-back must be declarative desired-state → read-current-source → pure diff → one greedy
  idempotent action (the §3.3.3 governor shape), so a double-fired kill/swap and a replayed Release
  are provable no-ops; add a `swap_is_idempotent` test alongside `restore_is_idempotent`.
- **AMEND §6 and §9 — reconcile the no-black-wallpaper invariant with the v0 ship plan (the brief
  contradicts itself).** §6 claims "no window of UE-dead-shader-not-yet-up"; §9 ships v0 with the
  honest ADR-0004 ~800 ms kill/relaunch flicker — which IS that window. The §6 invariant is a
  property of the UNBUILT crossfade tx, NOT of the kill floor the brief calls proven. Either (a)
  declare the ~800 ms flicker an accepted, time-bounded calm cost for v0 (hand the visible cost to
  `ux-reviewer`), or (b) make swap-up-then-kill non-deferrable. **The brief cannot both defer the tx
  (§9) and claim the tx's invariant holds (§6).**
- **AMEND §6/§10 — define revert ordering when the wallpaper source is changed externally** (the
  user, or another desktop agent per CLAUDE.md) while UE is killed-to-shader. "Relaunch UE +
  swap-back on Release" must be a DEFINED outcome (refuse-and-leave-user-choice, or three-way),
  never a silent last-writer clobber. Single-writer discipline covers `desired_rung` but NOT the
  shared wallpaper-plugin config.
- **NAMESPACE note (§4/§5):** the runtime source-swap apply/rollback tx must be named distinctly
  from the existing `crates/agentosd/dist/{apply,restore}.sh` systemd installer scripts (the
  installer is not the tx).

**Blocker (recorded, do not flatten):** the source-swap tx engine + ledger + fsync discipline **do
not exist** (grep confirms zero `tx` module, zero source-swap/crossfade/wallpaper-select code,
zero fsync in the daemon), and the headline atomicity claim is unsound as drawn. **This lane GATES
a user-facing ship: the one irreversible/desktop-mutating act in the whole design (the kill that
swaps the wallpaper source) MUST NOT be wired until R1–R5 are green.** The throttle-only loop
(Layers 1–2, no kill) is NOT gated. **Hand-offs:** whether the surface-up ack + the UE-as-wallpaper
plugin swap are mechanically achievable → `wayland-computeruse-reviewer` (this lane owns only that
the swap is atomic/reversible once it exists); whether the v0 ~800 ms flicker is an acceptable
surfaced cost → `ux-reviewer`; the kill/swap is the deterministic gate (no model output reaches it)
and shares the single-writer-on-wallpaper-source concern → `determinism-safety-reviewer`.

### 12.5 wayland-computeruse (BLOCK · 4/10 · gates ship)

**Ratifies:** the brief is honest that the controller is **inert without a resident UE surface**
and defers it to this lane (§8, §4 item 5) rather than pretending it is solved; the RC channel is
grounded in verified engine source (loopback bind via `[HTTPServer.Listeners]`, websocket
0.0.0.0-default disabled, Epic's own "do not open to the Internet" warning); the daemon-owned rung
allowlist is the correct agentosd→UE shape (ADR-0013); and the offscreen-vs-onscreen tension is at
least surfaced (`-RenderOffscreen` measurement vs a windowed wallpaper launch).

**Binding required additions:**
- **AMEND §4 item 5 + §8 — DELETE "KDE Application-Wallpaper plugin": no such plugin exists on this
  box** (verified — `/usr/share/plasma/wallpapers/` holds only `org.kde.{color,haenau,hunyango,image,potd,slideshow,tiled}`,
  all QML image/video/color renderers). Replace with the only proven mechanism: a thin
  Plasma/Wallpaper QML plugin (the `com.nimbus.flux/main.qml` pattern) that spawns the UE process,
  which must itself draw onto a **wlr-layer-shell / `org_kde_plasma_shell` BACKGROUND surface** —
  and state explicitly that this requires UE-side Wayland layer-shell role binding that **SDL2 does
  NOT provide**, making it net-new engine/shim work, not "wiring."
- **AMEND `packaged_run.md:37-38,:59` + §4 item 5 — STRIKE "KWin reparents it to the wallpaper
  layer."** This is a false X11-era assumption: KWin does NOT reparent foreign Wayland toplevels
  into the background layer; UE/SDL2 binds no layer-shell role and comes up as a normal floating
  xdg-toplevel ABOVE the desktop. Name the concrete options the spike must pick among: (a) a UE
  Wayland-layer-shell plugin/patch, (b) an external compositor-embed shim re-presenting UE's buffer
  onto a layer-shell surface, or (c) abandon native-UE-as-wallpaper and treat UE as an off-surface
  renderer feeding the existing flux/shader layer.
- **AMEND §5 — add a binding RC hardening clause (this lane's security call on backend privilege):**
  loopback bind is necessary but NOT sufficient — `:30010` is reachable by ANY local process and
  accepts arbitrary `ExecuteConsoleCommand` (the daemon-owned allowlist constrains only the
  agentosd→UE direction; loopback is a host boundary, not a process boundary). Any browser tab via
  DNS-rebinding-to-127.0.0.1, any MCP server, any malware-as-user can PUT and exec console commands
  inside the wallpaper — and **PythonScriptPlugin is enabled** (per `launch.sh`), making this a
  local code-exec surface. Require at minimum one of: (1) a Unix-domain/abstract socket reachable
  only by the agentosd uid, (2) a network namespace shared only with agentosd, or (3) an
  Origin/Host-header allowlist + a per-boot shared secret token agentosd injects via `-ExecCmds`
  and requires on every PUT. Residual supply-chain/secret-management → `security-reviewer`; the
  bind-scoping decision is this lane's.
- **AMEND §1/§8 — every footprint number is `-RenderOffscreen`; the wallpaper is ONSCREEN** with a
  real swapchain + presentation + compositor copy that were never measured. Re-measure FULL/REDUCED/FLOOR
  VRAM + util for the actual onscreen layer-shell/embedded surface before the throttle budget is
  locked (new gate, below).
- **AMEND §6/§8 — multi-output + fractional-scaling clause:** `packaged_run.md` hardcodes
  `-ResX=2560 -ResY=1440 -WinX=0 -WinY=0`; a layer-shell background surface is per-output and UE has
  no notion of Plasma output or scale factor. Specify which output the surface binds, how it tracks
  output add/remove and per-output scale (Plasma 6 fractional scaling), and how `r.ScreenPercentage`
  composes with output scale.
- **AMEND §6 — compositor/surface-lifecycle interlock:** an in-flight RC throttle or kill-to-shader
  source-swap must be interlocked against a KWin restart / output-change / layer-shell teardown, so
  an "acting" actuation never targets a process whose surface was pulled (extends the `wind.rs`
  lock-isolation precedent to the surface lifecycle, not just the lease lock).
- **AMEND §9 v0 item 2 — idempotent verified teardown:** on every kill/relaunch assert no orphaned
  `UnrealGame` PID, no leaked layer-shell surface, and that `:30010` is released before relaunch (no
  collision/silent-bind-fail) — citing the existing 8 h VRAM-zombie incident (README gotcha #8, #2).

**Blocker (this is a BLOCK, not an amend — do not soften it):** the foundation the controller
stands on rests on **two false Wayland assumptions verified on this box** — (1) there is no "KDE
Application-Wallpaper plugin," and (2) KWin does not reparent foreign toplevels into the background
layer, and UE/SDL2 binds no layer-shell role. The ONLY proven external-GPU-process-as-wallpaper
path is the nimbus-flux pattern (a QML plugin spawning an engine that itself draws on a wlr-layer-shell
background surface; KWin 6.6.5 confirmed to advertise `zwlr_layer_shell_v1` v5 +
`org_kde_plasma_shell` v8), and **UE does not speak that protocol.** Separately, the unauthenticated
`:30010` RC server is a local-process ambient-authority hole the allowlist does not close. **A
throttle controller with no proven resident wallpaper surface is inert by the brief's own admission
(§8); the surface-feasibility spike (G-WAYLAND-SURFACE) and the RC-access scoping (G-RC-ACCESS) must
clear before ANY lease-core surgery.** This is not a reason to abandon ADR-0023, but it is a reason
the controller MUST NOT enter build. **Hand-offs:** the `:30010` attack surface beyond bind-scoping
(DNS-rebinding, secret/token management, PythonScriptPlugin code-exec, RC plugin supply-chain) →
`security-reviewer`; the desktop→engine signal posture for the new wallpaper consumer →
`responsible-ai-privacy-skeptic` (already named §8); the surface-state completeness for revert →
`reversibility-tx-reviewer` (jointly ours); the GPU-pressure compositor/wallpaper kill-relaunch
mechanics → `resource-safety-reviewer` (the surface-lifecycle interlock depends on their restart model).

---

### 12.6 New gates added

The five lanes add gates beyond the existing **G1–G4** (Phase-A measurement gates, §8). Folded into
one continuing numbering (G5 onward), all binding:

| Gate | Lane | What it asserts | Blocks |
|---|---|---|---|
| **G5 — onscreen footprint re-measure** | wayland-computeruse | re-measure FULL/REDUCED/FLOOR VRAM + util for the ACTUAL onscreen layer-shell/embedded surface (swapchain + KWin compositing), not `-RenderOffscreen` | the Phase-B throttle budget (footprint math unvalidated for the shipped surface until green) |
| **G6 — RC auto-start in a real cook** | wayland-computeruse | confirm `:30010` actually opens on the real packaged cook (standalone `-game` auto-start is reported flaky); if not, the live-throttle claim downgrades to launch-time-rung + kill-floor only | the brief's live-throttle product claim |
| **G-WAYLAND-SURFACE** | wayland-computeruse | a packaged UE 5.8 `-game` build actually presenting on a Plasma 6 / KWin 6.6.5 wlr-layer-shell / `org_kde_plasma_shell` BACKGROUND surface (below normal windows, above the QML backdrop) for an hours-long run — NOT a floating xdg-toplevel | **everything — the controller MUST NOT enter build until this passes** |
| **G-RC-ACCESS** | wayland-computeruse (+ security-reviewer) | prove `:30010` is reachable ONLY by agentosd (socket scoping / netns / per-boot token + Origin allowlist) and that a hostile local client cannot exec console/Python commands in the wallpaper | ship |
| **R1 — source-swap round-trip** | reversibility-tx | apply→revert→assert wallpaper source + plugin config + UE rung bit-identical to pre-swap, over a REAL surface, before any kill is wired | the UE kill path |
| **R2 — surface-up verification** | reversibility-tx | no SIGKILL fires until a positive "first shader frame composited" ack lands (no black-wallpaper window) | ship |
| **R3 — crash-mid-swap recovery** | reversibility-tx | inject a daemon crash between surface-mutate and ledger-fsync; on restart the wallpaper resolves to a DEFINED, durable state (requires fsync(file)+fsync(dir), of which the daemon has ZERO) | ship |
| **R4 — idempotent swap** | reversibility-tx | double-fired kill/swap and replayed Release each proven a no-op by a `swap_is_idempotent` test | ship |
| **R5 — compositor-under-load** | reversibility-tx | the surface-up ack must not false-positive while a concurrent ~17 GB allocation stalls KWin | ship |
| **A11Y-GATE-1 — reduce-motion reaches the lever** | ui-accessibility | with `prefers-reduced-motion` set, UE is camera-frozen + `MaxFPS`-capped AND busy is still conveyed by a non-color shape+label channel, verified independently of throttle rung | user-facing ship |
| **A11Y-GATE-2 — FLOOR-frame contrast** | ui-accessibility | AA audit (4.5:1 text / 3:1 UI) of every overlaid surface against the DIMMED/COOLED FLOOR frame + the snag frame, not the FULL idle baseline; PR screenshots include the FLOOR frame | user-facing ship |
| **A11Y-GATE-3 — SR status message** | ui-accessibility | throttle-onset, FLOOR, blind/stale, and restore each emit a polite (assertive for blind/snag) live-region announcement, keyed off the same derived field as the visual cue | user-facing ship |
| **A11Y-GATE-4 — flash rate** | ui-accessibility | extends **G4**: no large-area luminance flash across any rung transition crosses WCAG 2.3.1 (3 flashes/sec), including when a transition coincides with another reactive bloom | user-facing ship |
| **A11Y-GATE-5 — blind ≠ idle, legible <1 s** | ui-accessibility | the stale/blind cockpit tell is visually AND audibly distinct from idle within 1 s of the RC channel going dark, carried by shape+label+heartbeat, not color/sweep alone | user-facing ship |
| **SOUND-GATE-1 — byte-silent yield** | sound | a throttle rung-change, a governor tick, and a kill→relaunch flicker are byte-silent and emit no swaync toast — verified across a forced FULL→FLOOR→SHADER sequence | user-facing ship |
| **SOUND-GATE-2 — suppression** | sound | a real `needs_you` firing DURING an in-flight yield/relaunch is suppressed/deferred — no earcon lands on the ~800 ms flicker window | user-facing ship |
| **SOUND-GATE-3 — never sound-only** | sound (co-owned with ui-accessibility) | under reduce-motion the yield remains legible via the §6 static dim/tint step with NO audio substitute | user-facing ship |
| **PRIVACY-GATE-1 — label-only on the wire** | privacy | `throttle.json` + the `lease.json` reason field carry only a sanitized holder LABEL (`lease.rs:313-332` allowlist) + a fixed reason ENUM — never raw argv/cmdline/task title/`last_preempt` narration; enforced by `holder_field_is_label_allowlist_only` | binding before build |
| **PRIVACY-GATE-2 — runtime-dir + clean uninstall** | privacy | `throttle.json` lives in `$XDG_RUNTIME_DIR/nimbus-aurora` at 0700 (tmpfs, logout-ephemeral), atomic+edge-written byte-stable at idle; `restore.sh` deletes it and the new units; `--purge` reaches all new state | binding before build |
| **PRIVACY-GATE-3 — consent + disclosure** | privacy | a named consent + disclosure moment for the resident UE wallpaper (opt-in distinct from agentosd install) and an updated telemetry disclosure line covering the new yield events in `telemetry.jsonl` | user-facing ship |

### 12.7 Revised GO/BLOCK verdict

**The design does NOT clear for build, and does NOT clear for a user-facing ship. One lane returns
an outright BLOCK; the other four gate ship. Only the v0 substrate-loop proof (Layers 1–2, stepped
cvars behind a flag, NO kill) proceeds.** Stated plainly, preserving dissent:

1. **`wayland-computeruse-reviewer` returns verdict = BLOCK (4/10).** The resident wallpaper
   surface — the foundation without which the whole controller is inert (§8) — has **no proven
   mechanism** on this box: there is no KDE Application-Wallpaper plugin, KWin does not reparent
   foreign toplevels into the background layer, and UE/SDL2 binds no layer-shell role. **Build is
   gated on an unbuilt, unproven precondition: G-WAYLAND-SURFACE must pass before ANY lease-core
   surgery.** This is not softened to "amend" — it is a block on entering build. G-RC-ACCESS
   (the unauthenticated `:30010` local-code-exec hole) must clear before ship.

2. **`reversibility-tx-reviewer` (5/10) gates the UE-kill path on an UNBUILT tx.** The source-swap
   tx engine, ledger, and fsync discipline **do not exist** (zero in the tree), and the §6
   "no-black-wallpaper" atomicity claim is unsound as written and contradicted by §9's own ~800 ms
   v0 flicker. **The kill path MUST NOT be wired until R1–R5 are green.** The throttle-only loop is
   not gated by this lane.

3. **`ui-accessibility-reviewer` (5/10) and `sound-designer` (7/10) jointly gate the user-facing
   ship** — consistent with the §11 dissent that the sibling `ambient-calm` lane (also a 5) blocks
   ship on the same transition/FLOOR/stale work. The four §6 a11y clauses (non-color reduce-motion
   encoding, SR-exposed blind tell, FLOOR-frame contrast, bounded flash rate) and the silent-yield +
   suppression + never-sound-only sound posture are **ship-gating, not fast-follow polish**. The
   single most load-bearing a11y gap: the keyhole has **no screen-reader live-region today**
   (`README.md:249`), so "never let blind == serene" currently holds only for sighted users.

4. **`responsible-ai-privacy-skeptic` (6/10) gates the user-facing ship** on identity discipline
   (`throttle.json` + the `lease.json` reason field must carry a label, not identity), runtime-dir
   placement + clean uninstall, the persistent-telemetry yield-trace, and a **named consent moment
   for the resident UE** (installing a systemd unit is not consent). None are architectural — the v0
   loop can proceed with a label-only, enum-reason, runtime-dir-0700 `throttle.json`.

**Net verdict: BLOCK on build (wayland-computeruse) + GATED on ship (all five).** The brief's own
posture survives intact — it is a *proposal-of-a-proposal* (§0, §8), and these five reviews convert
four "consult"/"open question" placeholders into binding gates plus one hard block on the surface
foundation. **What proceeds unblocked:** G1–G4 measurement (§9 item 1), and the v0 substrate-loop
proof (`Tier::Yielding` + `Throttle` + `fits_after_throttle` + the supervisor-owned bounded confirm
that falls through to the existing `Reclaim::Spawned` kill, behind a flag, with stepped cvars and no
user-facing surface). Everything that touches the resident UE surface, the UE kill/source-swap, or a
user-facing demo is gated as above. No dissent has been flattened; the wayland BLOCK is recorded as a
block, not an amend.
