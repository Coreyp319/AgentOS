# ADR-0029: UE-as-wallpaper is primary; the procedural shader is the fallback floor (inverts ADR-0009 §1, extends ADR-0023)

- Status: Proposed
- Date: 2026-06-19
- Supersedes (in part): [ADR-0009](0009-dreaming-shader-primary-video-as-texture.md)
  **decision §1** ("the procedural shader is the *permanent primary* renderer of agent state")
  and the consequence "**3D is still deferred, not dropped**." This ADR inverts the
  primary/fallback ordering for the *ambient wallpaper surface only*: a live UE 5.8 (Lumen)
  environment becomes primary; the procedural shader becomes the mandatory **fallback floor**.
  It does **not** disturb ADR-0009 §2 (two-surfaces/two-media split), §3 (own-PID + admission
  + SIGKILL eviction), §4 (naming), or the idle-byte-identical contract — those carry forward
  and constrain this decision.
- Extends: [ADR-0023](0023-creative-environment-pipeline.md) — the creative-environment pipeline.
  The brief contract, the dual-purpose path spline (the "ride vehicle"), the bounds/clip
  validator, the locked palette, the SemVer schema, and the `window-drag → wind` producer→sink
  are **kept**; this ADR re-targets their *output renderer* from Blender-EEVEE-artifact /
  shader-as-texture to a live UE real-time stage (the "Disneyland dark-ride" framing).
- Relates to: ADR-0001 (substrate, not orchestrator — reuse the producer→consumer grammar, do
  not reinvent it), ADR-0003 (fail-open supervised — the shader floor *is* the fail-open state),
  ADR-0004 (graphics yield: kill/relaunch — the floor under the new throttle), ADR-0005
  (apply/rollback tx — any UE↔shader source swap that mutates desktop state must route it),
  ADR-0010/0013 (VRAM coordinator + lease lifecycle — the new `Tier::Yielding` amends these),
  ADR-0012 (keyhole — the management cockpit, a *distinct* surface from the wallpaper stage),
  ADR-0018 (VRAM coexistence budget — UE's throttled floor becomes a budget line),
  ADR-0022 (creative-app MCP — the lease floor; UE needs a Spawn/process-group profile, not
  `AdoptScope`, since UE is not a flatpak scope)
- Evidence (spike, throwaway): [`spikes/ue-probe`](../../spikes/ue-probe/README.md) — Phase-A
  packaged-runtime VRAM/GPU-time measurement; [`spikes/ue-probe/indigo_channel_setup.py`] —
  the first landed tableau ("The Indigo Channel").

## Context

ADR-0009 established the procedural aurora shader as the *permanent primary* renderer of ambient
agent state, with 3D "deferred, not dropped" behind the same `agent.json` seam. ADR-0023 then
fulfilled that reserved 3D slot **inside** ADR-0009's grammar: a prompt → coherent themed 3D
environment, delivered as a reversible *artifact* (Blender-EEVEE), with live interactivity carried
as a *shader-as-texture* uniform — UE explicitly *not* the wallpaper.

On 2026-06-19 the user (Corey) **rejected the shader-over-generated-photo realization as a "fake
skin"** ("putting shaders over top of a generated image… so far from what I wanted"). The vision
was restated and is the change this ADR ratifies:

- **The desktop wallpaper should *be* a live UE 5.8 (Lumen) real-time environment** — a continuous
  "Disneyland dark-ride": composed tableaux bound to t-ranges on one camera track (the ADR-0023
  dual-purpose spline is the ride vehicle) — **not** a baked render and **not** a 2D photo warped
  by a shader. UE on Linux/Vulkan/Lumen real-time is exactly the strength ADR-0022 §8 did *not*
  defer (that §8 gate is the *offline film render* / Path-Tracer, Windows/DX12-only — a render
  gate, not a real-time gate).
- **VRAM (and now GPU-time) management IS the product.** The flagship demo is a live UE wallpaper
  coexisting on one RTX 4090 with the user's ComfyUI/lucid gens + Ollama inference, never OOM,
  never freeze. If the substrate can't manage that dance, the project's thesis fails.

**Why the inversion forces new substrate behavior (the crux).** The substrate is **kill-and-reload
by measured necessity** — ComfyUI `/free` freed 0 MiB (ADR-0009 §3 evidence); SIGSTOP frees 0; the
only trusted reclaim is SIGKILL of an owned PID (or `cgroup.kill` of a flatpak scope, ADR-0022).
But a wallpaper **must not be killed to black** — kill-to-black is forbidden by the calm/honest
ambient contract — **and UE on Linux/Vulkan crashes rather than degrades under VRAM pressure.** So
a persistent UE wallpaper needs *yield-and-restore*, which the current 3-tier kill-only lease has
no representation for (Interactive never yields; Batch gets SIGKILLed; UE fits neither).

**What Phase-A measured (the gate, before any architecture — `spikes/ue-probe`).** A packaged
Development Linux `-game` build of a minimal scene boots offscreen, loads in 42 ms, Vulkan, no
crash. **FULL (native Lumen, 1440p, uncapped) ≈ 1.0–1.2 GB** (per-process 1187–1201 MiB;
card-delta ~1.3 GB; GPU util 96–100%). **FLOOR (Lumen GI+Refl off, pool-cap 512, 5 fps) ≈ 1.0 GB**
(per-process 970–980 MiB; card-delta agrees within 3% — per-process is *not* undercounting this
Vulkan workload here; util 39%). Two reads shape this decision: (1) **VRAM feasibility is emphatic**
— a packaged Lumen wallpaper is ~1 GB (vs ~22 GB for the editor), leaving ~23 GB for gens/models;
(2) **on a trivial scene the throttle lever is GPU-*time* (96%→39% util), not VRAM** — only ~250 MB
freed FULL→FLOOR; the ~1 GB base dominates and does not shrink. (Caveat the budget must respect: a
*richer* dark-ride tableau — textures/Nanite/more Lumen surfaces — will grow VRAM and make pool-cap
+ Lumen-off yield more; re-measure on a representative-richness scene before locking the Phase-B
budget.)

**First tableau, landed.** "The Indigo Channel" (`spikes/ue-probe/indigo_channel_setup.py`) — a
backlit volumetric-fog corridor (cyan backlight, dark blade-silhouette occluders, real depth/
shadows) — was authored headlessly and **approved on look** by the user ("has some mood to it"). It
runs **live** as a `-game -windowed` proc on this Wayland + Plasma 6 box (SDL `wayland` driver,
`VK_KHR_wayland_surface`, ~1–2 GB, coexists with gens), with a looping `LevelSequence` parallax
motion in build. This proves **UE-runs-live-on-Wayland** — but not the wallpaper *layer* (see Open).

## Decision

1. **For the ambient wallpaper surface, a live UE 5.8 (Lumen) real-time environment is PRIMARY;
   the procedural aurora shader is the mandatory FALLBACK FLOOR.** This inverts ADR-0009 §1 for
   this surface only. The shader is never decommissioned and never demoted to "off" — it is the
   *degraded, fail-open render* (ADR-0003) the wallpaper drops to when VRAM/GPU pressure forces UE
   down or off. **Kill-to-black is forbidden;** the shader floor (or a still) is what a UE kill
   reclaims *to*, never a blank wallpaper.

2. **UE renders the dark-ride; the shader floor renders agent state — both still honor the
   ADR-0009 ambient contract.** The continuous `{busy, warm, snag}` signal and the idle =
   byte-identical-to-the-unmodified-wallpaper invariant are **not** repealed. The shader floor
   carries them exactly as today. The live UE stage is held to the same calm/honest mapping (a
   tableau that reads as an attention magnet is a defect, same tripwire as ADR-0009/0023). *Open:*
   how the continuous floats map onto a UE real-time stage (vs. a shader uniform) is an unfinished
   design point routed to `art-director` + `motion-designer` + `design-technologist`, not decided
   here.

3. **Preemption against UE is PROACTIVE THROTTLE-not-kill, on a new `Tier::Yielding` (amends
   ADR-0010/0013), with kill→relaunch-to-floor as the backstop.** Because UE crashes rather than
   degrades under VRAM pressure, the coordinator must **shrink UE *before* a gen job allocates**,
   not react to telemetry pressure after. The trigger is a lease *arbitration event*. The throttle
   is a non-destructive `Throttle{to_floor}` over UE Remote Control (`r.ScreenPercentage` / `sg.*`
   / `t.MaxFPS` / streaming pool — the cvar ladder in `spikes/ue-probe/cvar_ladder.md`), falling
   *through* to the existing own-PID SIGKILL + relaunch-to-shader-floor (ADR-0004) if the throttle
   does not free enough or UE misbehaves. **Hard invariant: the lease owns the kill; the governor
   can only *ask* UE to shrink.** The throttle path takes **no arbitration lock and can never delay
   a preemption SIGKILL** (the resource-safety load-bearing condition, inherited from the ADR-0023
   `wind.rs` lock-isolation guarantee). The architecture is "D · Bracket" from the Phase-B council
   pass. *This tier is DESIGNED and PAUSED* (see Consequences) — its construction is gated on the
   wallpaper-layer probe (Open §A), since there is nothing to throttle until UE is the wallpaper.

4. **UE's throttled FLOOR footprint is a first-class VRAM-budget line (amends ADR-0018).** Because
   the product is *curated coexistence* (pack the 4090 with as many useful co-resident models as
   fit, not serial eviction), every GB UE sheds at the floor is a GB freed for another co-resident
   model. Admission of higher tiers is computed **against UE's throttled floor**, not its full
   footprint. The two-number footprint (full ~1.2 GB / throttled-floor ~1.0 GB on the *trivial*
   scene) must be re-measured on a representative-richness tableau before the budget is locked.

5. **Performance (GPU-time) is a co-equal metric to VRAM for this surface.** The dark-ride tableaux
   must render *cheap* in GPU-time (on-rails camera through discrete tableaux is the most
   optimizable shape: level-stream only the current vignette, Nanite, hard FPS cap 15–30, software/
   reduced Lumen at the floor) so a 24/7 wallpaper does not starve gens, stutter, or cook the card.
   The throttle ladder of Decision 3 is *also* the compute-budget ladder.

6. **The wallpaper stage and the keyhole cockpit are two DISTINCT surfaces (preserves ADR-0012).**
   The UE wallpaper is the *stage*; the keyhole tray (ADR-0012) is the *management cockpit* that
   shows and controls the VRAM/throttle dance and carries its own tasteful GPU-driven effects. The
   animation does **not** live in the keyhole. Conflating them is a known error and is forbidden.

7. **Any UE↔shader source swap that mutates desktop wallpaper state routes the ADR-0005 apply/
   rollback tx.** "Fall back to the shader floor" as a *degraded render* under the same renderer is
   not a tx event (it is fail-open, ADR-0003). But if the *wallpaper source itself* is swapped as a
   desktop-state mutation (the layer host changes what presents), that swap is atomic, diffable, and
   revertible through the ADR-0005 tx — never a half-applied wallpaper. (The fallback-surface design
   "C" — UE rendered off-surface into the aurora `ShaderEffect` as a re-graded `dreamTex`, which
   would have *retired* the source-swap tx entirely — was scored highest by the Phase-B council but
   **rejected by the user as a vision compromise** (it demotes UE to a texture, the same "fake skin"
   objection). C is **kept only as the documented fallback**, not the path.)

## Consequences

- **Honest record.** This ADR is *Proposed* (ADR-before-code discipline). It is the ratifying ADR a
  rater correctly flagged as **owed** for the ADR-0023 pivot — the pivot has lived in memory and
  spikes; this closes the drift-without-an-ADR gap. The user disposes; nothing here ships on its own.

- **What is DECIDED vs OPEN — read precisely:**
  - **DECIDED (subject to ratification):** the *direction* — UE-primary / shader-floor inversion
    (D1), the ambient-contract carry-forward (D2), the proactive throttle-not-kill `Tier::Yielding`
    *architecture* (D3 = "D · Bracket"), UE-floor as a budget line (D4), GPU-time co-equal (D5), the
    two-surface separation (D6), and the source-swap → ADR-0005 routing (D7).
  - **PROVEN in spike (risk retired, code NOT in the crate):** packaged-runtime VRAM/GPU-time
    feasibility (~1 GB floor, GPU-time is the lever); a tableau look approved ("The Indigo Channel");
    UE runs **live-windowed** on this Wayland box.
  - **BUILT + TESTED 2026-06-20 — the `Tier::Yielding` decision core + the governor's pure half.**
    `crates/agentosd/src/coord.rs`: the new `Tier::Yielding` tier (lowest priority — UE yields to
    every workload — with `from_arg`/`as_str` round-trips and the existing Ord/clamp honoring it), and
    `yield_decision` — the pure, saturating **throttle-vs-kill** call computed against UE's
    **throttled-floor** footprint, not its full one (the D4 two-number-footprint invariant: a gen that
    won't fit beside UE-full but fits beside UE-floor reads `ThrottleAndCoexist`; otherwise
    `KillToShaderFloor`). `crates/agentosd/src/governor.rs`: the throttle ladder as typed `Rung`s
    (verbatim `cvar_ladder.md`), the **security-critical `is_allowed_cvar` allowlist** that fences the
    Remote Control channel to the closed set of ladder `(cvar, value)` pairs (D1 — the generic
    `ExecuteConsoleCommand` path is refused by construction, value-scoped so a gen can't smuggle
    `t.MaxFPS 999`), and `plan_preemption` mapping the decision to `Throttle(Floor)` | `Kill` (the
    governor only ASKS UE to shrink; the lease owns the SIGKILL — D3 in the type). Wired into the lease
    preempt path as an honest decision-LOG (`lease.rs`, off-lock): a `Yielding` victim's governor
    decision is computed + narrated, while the SIGKILL backstop still runs. 12 new tests; full suite +
    clippy green. **STILL UNBUILT (the gated remainder):** the hardened Remote Control TRANSPORT (the
    loopback-asserted, rebinding-aware HTTP client that sends a rung — gated on the §B lockdown below),
    the lease-side **coexistence model** (keep UE resident at floor while a gen holds the lease — the
    single-exclusive lease can't yet represent it), the keyhole control back-channel, and the dark-ride
    sequencer.
  - **OPEN / NOT ratified — do not overclaim:**
    - **(A) RESOLVED 2026-06-20 — UE CAN be a native Wayland wallpaper on this box (the original
      premise here was STALE).** KWin 6.6.5 advertises `zwlr_layer_shell_v1` v5; a foreign surface on
      the BACKGROUND layer composites at stacking index **[1]** — above the Plasma desktop
      containment, below every app window and the panel — and that role is injectable directly on
      UE's SDL3 `wl_surface` via `SDL_PROP_WINDOW_CREATE_WAYLAND_SURFACE_ROLE_CUSTOM`. Proven
      end-to-end on hardware (rung 1: gtk4 layer-shell stand-in; rung 2: SDL3 custom-role + Vulkan
      swapchain on a BACKGROUND surface) and **delivered live** (rung 3: the Indigo Channel running
      as the desktop wallpaper). Effort re-scoped from "large engine/compositor fork" to a
      **localized ~15-line `LinuxWindow.cpp` patch** (native path) or **zero engine changes**
      (keep-below KWin rule). One structural cost: Plasma fuses the wallpaper and desktop icons into a
      **single** containment surface, so any foreign wallpaper covers the icon grid — the user
      **disposed (2026-06-20) to give up the desktop-icon grid** rather than demote UE to a texture.
      The `org_kde_plasma_shell` *Desktop role* was ruled out (one desktop-surface per output, owned
      by plasmashell, not SDL3-injectable). Evidence: `spikes/ue-probe/{wallpaper_role,sdl3_vulkan,ue_wallpaper}`.
      See the resolved Open questions §A below for the remaining productionization choice.
    - **(B) The Remote Control server (`:30010`) is an unauthenticated local-code-exec hole** and
      must be locked down before the throttle channel ships. Routed to `security-reviewer`.
      **SHARPENED 2026-06-20 (governor-build + security review).** The lockdown is *deeper than
      loopback-binding*: the mechanism `spikes/ue-probe/remote_control_setup.md` documents for pushing
      a rung — `ExecuteConsoleCommand` via `PUT /remote/object/call` — **IS** the arbitrary-local-code-
      exec primitive (it runs any console string). So "lock down RC" is **not** "send only ladder cvars
      over the generic endpoint" (that endpoint stays a code-exec hole regardless of *what we* send);
      it requires **DISABLING the generic call endpoint and exposing instead a net-new UE-side narrow
      `UFUNCTION`** (e.g. `AgentOSThrottle.ApplyRung(int)`) that takes a **rung INDEX** and maps it to
      the ladder cvars *inside UE* — so the allowlist is enforced engine-side and the wire carries an
      integer, never a command. That UFUNCTION is **net-new UE/Blueprint code requiring a running
      engine to verify** (the `[VERIFY-LIVE]` question of ADR-0030 D1: does a hardened params-only RC
      channel prove clean on UE 5.8?). The Rust governor's **client-side half is BUILT** —
      `governor::is_allowed_in_rung` (rung-scoped, value-scoped, cross-rung-refusing) + `Rung` as the
      atomic unit of authorization — and the future hardened HTTP client MUST additionally: assert a
      **literal `127.0.0.1` bind at runtime** (refuse any non-loopback target by construction; no
      env-overridable host), treat **DNS-rebinding / browser-reachability as in-scope** (validate
      `Host`/`Origin`, reject non-loopback `Host`), send cvars as **structured fields** never a
      concatenated console string, and **charset-validate** every value (`^[A-Za-z0-9._-]+$`, already
      pinned by a governor test). Until the UE-side narrow endpoint exists, the throttle channel cannot
      ship securely — the governor decision is computed + logged but **never actuated** (see the BUILT
      bullet above).

      **RE-GROUNDED 2026-06-21 (design-security + resource-safety review — both read the LIVE UE 5.8
      source on this box; advisory, no wire code written, per the chosen "close the gate first"
      sequencing).** Two corrections to the design above, then the buildable lockdown + the
      resource-safety ordering ruling.

      *Correction 1 — the lockdown is the engine's NATIVE default-deny allowlist, not a net-new
      `ApplyRung` UFUNCTION-as-the-primary (the §B text above over-built it; and "disable the generic
      endpoint" is not achievable — the `call`/`property` routes register unconditionally).* Verified in
      `WebRemoteControlInternalUtils.cpp:551-606` + `RemoteControlSettings.h:350-377`:
      `/remote/object/call` is already gated by `bAllowAnyRemoteFunctionCall=false` (default — a call
      resolves ONLY if its `(class,function)` is in `CustomAllowedRemoteFunctionCalls`), and
      `ExecuteConsoleCommand` is *separately* refused unless `bAllowConsoleCommandRemoteExecution=true`
      (default false), with Python-over-console independently blocked. So the code-exec primitive is
      closed by COOKED CONFIG (engine-enforced), not by what agentosd sends and not by Blueprint/C++.
      Required cooked posture, pinned in `DefaultRemoteControl.ini` + proven in the `-game` build:
      `bAllowAnyRemoteFunctionCall=false`, `bAllowConsoleCommandRemoteExecution=false`,
      `bEnableRemotePythonExecution=false`, `bRestrictServerAccess=true` (+ a non-`*` `AllowedOrigin`);
      and `CustomAllowedRemoteFunctionCalls` = EXACTLY `{KismetMaterialLibrary::SetScalarParameterValue
      (+SetVectorParameterValue), UAgentOSThrottle::ApplyRung}` — `ExecuteConsoleCommand` is NOT on it.

      *Correction 2 — "loopback is a trust boundary" is FALSE here; strike it.* Verified
      (`RemoteControlDefaultPreprocessors.cpp:249-282`, `WebRemoteControlInternalUtils.cpp:676-689`):
      UE's passphrase/Origin auth `Passthrough()`s for any `127.0.0.1`/`localhost` peer, so it never
      challenges us — nor any other local process / DNS-rebound browser. The ONLY inbound control is the
      allowlist above (which IS peer-agnostic, so it *does* bite local callers). The §B line "validate
      `Host`/`Origin`, reject non-loopback `Host` (DNS-rebind defense)" survives ONLY as an agentosd
      *client* self-check (refuse any non-`127.0.0.1` target), NOT as an inbound defense (UE is the
      server and does not do it). **Accepted residual, recorded honestly:** any local process can drive
      the bounded reactive MPC scalars and the rung index; RC grants no isolation between local callers
      on loopback. ACCEPTED because the allowlist caps blast radius to bounded wallpaper params + a
      clamped rung, the lane holds no secret, and `scene.rs` is structurally barred from the
      lease/SIGKILL path (`Copy`, no `lease::Inner`).

      *The two lanes, per the lockdown.* **Throttle (governor):** the ladder is console-only cvars
      (`sg.*`/`r.*`/`t.MaxFPS`), so with console-exec disabled the ONE justified net-new engine function
      is `UAgentOSThrottle::ApplyRung(int32 idx)`, idx∈{0,1,2}→fixed `Full|Reduced|Floor` cvar set mapped
      via `IConsoleManager` INSIDE C++ (strings never on the wire), idx clamped engine-side;
      `governor::is_allowed_in_rung` becomes the client-side defense-in-depth (`Rung→idx`). NEVER flip
      `bAllowConsoleCommandRemoteExecution=true` to "make the ladder work" — that reopens the whole hole.
      Restore-to-Full is `ApplyRung(0)`; never a `SetCvar(name,val)` convenience (un-throttling during
      another tenant's gen is the one availability attack this channel enables). **Reactive (scene
      pusher):** `SetScalarParameterValue`/`SetVectorParameterValue` via the allowlist — NOT a `SetMood`
      UFUNCTION, and NOT the `/remote/object/property` exposed-MPC-preset fallback (the property route
      bypasses the function allowlist → wider/weaker surface). `ParameterName` from a fixed Rust enum of
      the six names (never feed-derived); each scalar clamped client-side AND saturated engine-side in the
      material graph (the wire is unauthenticated, so any local caller could send NaN/1e9).
      `WorldContextObject:null` resolution is the one `[VERIFY-LIVE]`; the fallback is a thin allowlisted
      `UAgentOSReactive::SetMood(...)` that grabs the world itself — still never the property/preset
      route. **Build-time residual:** the snag→fog-density lever is an engine property with no MPC scalar
      and no global cvar under the lockdown — resolve at cook as an MPC-driven fog *material* parameter or
      a third allowlisted setter, never generic console.

      *Bind + least-privilege (the network is doing real authz work, per Correction 2).* The HTTP
      listener binds via the GLOBAL `[HTTPServer.Listeners] DefaultBindAddress` (NOT an RC property) — pin
      `127.0.0.1`; keep `bAutoStartWebSocketServer=False` (its bind defaults `0.0.0.0`); drop inbound
      `:30010`/`:30020` on non-loopback ifaces via nftables/firewalld (kernel-enforced, survives config
      drift); and STOP the RC server (`WebControl.StopServer`) whenever the shader floor — not UE — is the
      live wallpaper (exposure window shrinks to "UE is the active surface" only). The generic
      `call`/`property` routes are rendered INERT (allowlist + no exposed preset + bind + firewall), not
      absent.

      *Post-cook release gates (prove the lockdown, don't assume it).* In the `-game` build: (a) a raw
      `ExecuteConsoleCommand` PUT returns the "console commands … not enabled" rejection; (b) a
      non-allowlisted function (e.g. `KismetSystemLibrary::QuitGame`) returns "not allowed by remote
      control settings" (tripwire vs. a silent `bAllowAnyRemoteFunctionCall` regression — that flag opens
      *every* UFUNCTION); (c) `ss -ltnp` shows `:30010`/`:30020` bound to `127.0.0.1` only. Any failure
      fails the release.

      *Resource-safety ORDERING ruling (the load-bearing one).* Today `lease.rs:806-808 perform_reclaim`
      SIGKILLs every evicted owned victim UNCONDITIONALLY; the governor `ThrottleAndCoexist`-vs-`Kill`
      decision is computed only to LOG (`:816-837`). Therefore: **(1)** the throttle RC channel must NOT
      ship before the lease-side coexistence model — doing so manufactures a split-brain (UE *appears* to
      yield over RC while the lease SIGKILLs it anyway; or, if the lease later trusts an UNCONFIRMED
      throttle and skips the kill but the PUT silently failed, it admits a gen against VRAM never freed →
      the substrate causes the OOM, the cardinal sin). **Coexistence lands WITH the throttle, never
      before/after,** as **confirm-then-admit**: send rung → re-read NVML for the floor footprint within a
      bounded deadline → count `ue_full − ue_floor` as reclaimed ONLY when measured → else fall through to
      the existing SIGKILL→shader-floor backstop. One reclaim per event, chosen by measured outcome.
      **(2)** The reactive MOOD/MPC pusher is INDEPENDENT and unblocked now (never frees VRAM, never gates
      admission, a failed PUT is purely cosmetic) — it MAY be built ahead of coexistence, but MUST ship
      with the inherited safety contract: it owns only a `Copy` snapshot of `scene.rs`'s disposed scalars
      + an HTTP client + the literal-`127.0.0.1` target (no field/import/path to `lease::Inner`); it never
      holds a cell across the PUT `.await` (snapshot-by-value `SceneScalars: Copy`, drop, then await — so
      lock-across-await can't compile); per-PUT `timeout` ≈ tick period, single in-flight PUT over a
      latest-value cell (`watch`/`ArcSwap`, never an unbounded `mpsc`/retry), best-effort drop, UE holds
      last-good (blast radius of a hung `:30010` = one dropped frame); on a detected relaunch it resets the
      epsilon `last_sent` baseline to a sentinel so it force-re-converges UE from idle defaults (the
      relaunch signal arrives one-way, never by reaching into the lease lane); it consumes
      `scene-params.json` (the disposed frame), NOT a re-derivation of the feeds. It ships with a
      `pusher_takes_no_inner_lock` + `pusher_is_silent_at_rest` tripwire pair (the `wind.rs:379-392` /
      `scene.rs:980-989` analogue). **(3)** Two sinks, never one send-path: the mood sink (scalar setter,
      cosmetic) and the throttle sink (`ApplyRung`, integer, security-critical, gated on coexistence)
      share no transport, no allowlist entry beyond their own function, and no helper; `MotionSpeed` (the
      one scalar both lanes' concepts touch) stays governor-driven only — a mood push can never override a
      throttle.

      *Revised §B GO/NO-GO.* GO once: the four config flags are cooked + the three post-cook gates pass;
      the throttle `ApplyRung` UFUNCTION exists + is `[VERIFY-LIVE]`-confirmed AND coexistence/confirm-
      then-admit lands WITH it; the reactive wire is the allowlisted scalar setter (no preset/property);
      bind is loopback + firewalled + server-off-when-unused; and the two honesty edits above are in.
      NO-GO if any of: console-exec enabled to drive the ladder; the MPC driven via
      `/remote/object/property` preset; `bAllowAnyRemoteFunctionCall=true`; `:30010` bound to anything but
      loopback; or the throttle channel shipped without coexistence. Full findings: the security (F1–F10)
      + resource-safety review outputs of this session.

      **VERIFIED ON THE LIVE ENGINE 2026-06-21 — the §B lockdown ENFORCES (no longer just design analysis).**
      The lockdown config was authored on `~/UnrealProjects/AgentOSBlank` (RemoteControl plugin — precompiled,
      NO source build; `Config/DefaultRemoteControl.ini` default-deny allowlist of exactly
      `KismetMaterialLibrary::SetScalarParameterValue`/`SetVectorParameterValue`; console-exec/python-over-RC
      off; `DefaultEngine.ini [HTTPServer.Listeners] DefaultBindAddress=127.0.0.1`) and proven against a live
      offscreen `-game` run (`spikes/ue-probe/verify_rc_lockdown.sh`, all 5 gates GO): `ExecuteConsoleCommand`
      → **rejected 400** ("…is not allowed by remote control settings"), non-allowlisted `QuitGame` → **rejected
      400**, allowlisted `SetScalarParameterValue` → **accepted 200**, `:30010` **loopback-bound**, `:30020`
      **absent**. So the §B `[VERIFY-LIVE]` (does a params-only RC lockdown prove clean on UE 5.8?) is answered
      **YES**. The reactive MPC (`MPC_AgentOS_Reactive`, 8 scalars == `rc.rs` `AXES`) was authored the same
      session. The only §B-adjacent item still gated on the box is the production **cooked** package (the cook
      toolchain `RulesError`/receipt issue) — the GATE itself no longer depends on it (the verify used `-game`
      from the editor binary, identical config + RC code path). Throttle actuation remains gated on coexistence.
    - **(C) `capture_shot` offscreen self-verify is OVEREXPOSED** (SceneCapture2D self-auto-exposes,
      not yet exposure-matched to the `-game` runtime truth); and **motion auto-play in `-game` is
      pending live confirmation.** These are spike-verification gaps, not design decisions.

- **The Phase-B throttle controller is now UNBLOCKED (Open §A resolved 2026-06-20).** UE-as-wallpaper
  is real, so there is finally something to throttle. The cheap end of the ladder is already
  demonstrated: an FPS cap (`t.MaxFPS 30`) dropped a live Indigo-Channel wallpaper from **94% → 40%**
  GPU util at ~1.2 GB. **The substrate decision core landed 2026-06-20** (`Tier::Yielding` +
  `yield_decision` + the `governor` ladder/allowlist/plan — see the BUILT bullet above); what remains
  before the throttle actually fires is the **hardened Remote Control client** (gated on the §B
  lockdown) and the **lease-side coexistence model**. The procedural aurora shader **remains the
  default live wallpaper** until those ship — UE-as-wallpaper is opt-in via
  `spikes/ue-probe/ue_wallpaper/wallpaper_keepbelow.sh` today.

- **The concrete symptom the throttle cures — diagnosed 2026-06-21 (Lucid "first prompt resets,
  second renders").** A user-reported Lucid bug traced NOT to the turn/epoch race it looked like but to
  **cold-lease admission refused at the VRAM knife edge.** The journal (`agentos-lucid.service`) shows
  admissions denied by as little as **1-49 MB** (`free 18061M vs est 17000 + headroom 1062 = 18062M`),
  then granted on retry. Mechanism: only the FIRST beat of a session (or first after the 600 s idle-reap)
  cold-spawns ComfyUI and runs `admit()`; the **live UE wallpaper's ~6 GB baseline** tips free VRAM just
  under `est·17/16`, so `admit` denies → the beat fails open as `skipped` → the UI resets → the user
  re-submits and the second try (free ticked up) renders. Once warm the held token is reused with no
  re-admission, so only the first prompt flaps. **This is exactly what `Tier::Yielding` throttle-to-admit
  exists to fix:** throttling UE full→floor frees ~200-300 MB — more than enough to clear a 1-49 MB
  knife-edge shortfall — so `yield_decision` returns `ThrottleAndCoexist` and the dream admits on the
  first try WITHOUT killing the wallpaper; only a heavy shortfall (>throttle gain) falls through to
  `KillToShaderFloor`. Until the throttle ships, two interim mitigations landed (they MASK the symptom,
  they do not free VRAM — the throttle remains the structural fix):
  - **L1 — bounded retry before fail-open** (`spikes/dreaming/lucid/lucid_web.py`, `ADMIT_RETRIES`/
    `ADMIT_BACKOFF` in `_ensure_lease`): a transient admission refusal is retried (abortable on
    supersession) so the knife-edge flap self-heals on the first user action instead of surfacing a
    bare `skipped`. Regression test `test_transient_refusal_retried_then_granted`.
  - **L3 — est calibration VALIDATED, not shaved** (`lucid_linear.py` `_est_mib` doc): 80k
    `telemetry.jsonl` samples show the ComfyUI footprint mode is **16-17 GB** (= Q4 `est 17000`) and the
    card has hit **`free<1 GB`**, so est is correctly calibrated — lowering it to widen the margin would
    risk OOM. The asymmetry is decisive: a false refusal is a now-retried annoyance, an under-estimate
    is an OOM crash. Confirms the knife-edge is a real resource conflict (UE baseline vs a ~16-17 GB
    model on a 24 GB card), not a bad number — i.e. it confirms the throttle, not a recalibration, is
    the fix.

- **Sequenced on-box build plan for the throttle (the "what remains," 2026-06-21).** Grounded in a
  read-only wiring audit of the live daemon + UE RC. Steps B1-B3 are net-new/additive (no live-daemon
  risk); B4 is the safety-critical change; B5 is the gate.
  - **B1 — register UE as a `Tier::Yielding` OWNED holder** (`est = UE_FULL ≈ 1300`, so `yield_decision`
    sees UE's full footprint and `ue_floor_mib()` its floor — the D4 two-number invariant). Sufficient
    ALONE to stop the denial: `arbitrate(Yielding, Batch) → Preempt` installs the requester WITHOUT
    consulting `admit()` (the denial lives only in the `Grant` branch, `lease.rs` `acquire_with`). Two
    options: a new `ue-wallpaper` entry in the `PROFILES` allowlist (`lease.rs`) + `nimbus-ue-wallpaper`
    calling `Spawn yielding <est> ue-wallpaper` instead of `setsid -f`; OR a new PID-based `AdoptPid`
    verb (parallel to `AdoptScope`, which can't take UE — it's a bare process, not a flatpak scope) that
    adopts the running UE PID with a `/proc/<pid>/cmdline` allowlist match on `AgentOSBlank.uproject`.
    Caveat: `spawn_owned`'s `process_group(0)` + group-SIGKILL must reach UE's watchdog/detach model.
  - **B2 — author the UE project-C++ `UAgentOSThrottle::ApplyRung(int32 idx)` UFUNCTION** (idx∈{0,1,2}→
    `Full|Reduced|Floor` cvar set via `IConsoleManager` in C++; the Floor rung's cvars per `governor.rs`
    — `sg.GlobalIlluminationQuality`, `r.Streaming.PoolSize`/`LimitPoolSizeToVRAM`, `t.MaxFPS`). **Project
    C++ compiles against the Installed build — NO source-build gate** (that gate is only the layer-shell
    wallpaper-delivery patch, a separate concern). Harden `DefaultRemoteControl.ini`: params-only
    allowlist `{SetScalarParameterValue, SetVectorParameterValue, UAgentOSThrottle::ApplyRung}`,
    `bAllowConsoleCommandRemoteExecution=false`.
  - **B3 — author the Rust RC throttle client** — a SEPARATE sink from `rc.rs` (whose header forbids
    mixing the mood and throttle channels): reuse its loopback-literal guard + 250 ms timeout + redirect-
    none + structured `CallBody`, but PUT `ApplyRung(idx)` not `SetScalarParameterValue`. Client-side
    `Rung→idx` map is `governor::is_allowed_in_rung` as defense-in-depth.
  - **B4 — lease-side COEXISTENCE (the safety-critical change).** The single-exclusive holder model
    (`lease.rs`, one `holder`) cannot represent "UE resident-at-floor AND a gen holding the primary
    lease." Add a reservation/second-class-holder representation so the throttle path does NOT hit the
    unconditional `perform_reclaim` SIGKILL (`lease.rs:1080-1082`); wire `plan_preemption(Throttle)` to
    actuate B3, and fall through to `KillToShaderFloor` ONLY on `GovernorAction::Kill` OR a failed/timed-
    out throttle PUT. **Cardinal-sin guard (already noted in §B): never admit a gen against VRAM the
    throttle reported freeing but did not — coexistence lands WITH the throttle, never before.**
  - **B5 — `[VERIFY-LIVE]` on the box:** the throttle frees the measured ~200-300 MB, the first dream
    beat admits without a retry, UE stays at floor (never black), and the kill→shader-floor backstop
    fires when the throttle is insufficient. Closes the ADR-0030 D1 params-only-RC `[VERIFY-LIVE]`.

- **Throttle build — progress 2026-06-21 (the actuation half is built; the lease integration remains).**
  - **B3 BUILT + TESTED** — `crates/agentosd/src/rc_throttle.rs`: a SEPARATE RC sink from the mood
    `rc.rs` (per §B), PUTing `ApplyRung(idx)` to UE's loopback RC server. Mirrors `rc.rs`'s loopback-
    literal guard + redirect-none + 250 ms timeout (its OWN copy, so neither channel can silently weaken
    the other), sends a rung INDEX never a cvar (`governor::Rung::index()` is the wire contract), and
    surfaces an honest `ThrottleOutcome` so the B4 cardinal-sin guard can refuse to admit on a non-`Applied`.
    6 unit tests + full suite 203/203, clippy-clean. Dormant until B4 calls it.
  - **B2 AUTHORED (box-compile gated)** — `spikes/ue-probe/throttle/`: the `UAgentOSThrottleLibrary::
    ApplyRung(int)` UFUNCTION (rung idx → fixed cvar set via `IConsoleManager` INSIDE the engine, so
    `ExecuteConsoleCommand` stays disabled), the minimal C++ game module to host it on the
    blueprint-only project, the `.uproject`/`.ini` patches, and `install_throttle_module.sh`. cvars
    mirror `governor.rs::Rung::cvars()`. Compile + the four-step live verify are the on-box gate (per the
    REFERENCE note, project C++ compiles against the Installed engine — only engine patches need a source
    build; that's the one assumption B2's verify confirms).
  - **B4 DESIGN — admission-side throttle, NOT a holder rearchitecture (refines §3's mechanism).** The
    single-exclusive lease (`LeaseState.holder: Option<Held>`) can't represent "UE resident-at-floor AND
    a gen holding the lease" as two holders. Rather than rearchitect the GPU arbiter (high-risk surgery),
    register UE as a *throttleable wallpaper* (PID + full/floor + RC endpoint) and add a **throttle-
    before-deny** step to the admission shell, reusing the existing `reclaim.rs` reclaim-before-deny
    pattern: when a gen's admit would fail and `yield_decision == ThrottleAndCoexist`, call `rc_throttle`
    to drop UE to Floor, re-measure free VRAM, and only then admit (the cardinal-sin guard: never admit
    against VRAM a non-`Applied` throttle didn't free); restore UE to Full on the gen's Release;
    `KillToShaderFloor` keeps today's SIGKILL backstop. This achieves §D4 (admission against UE's
    throttled floor) and §3's throttle-not-kill **outcome** without touching `holder`/`arbitrate`/`Held`.
    B1 (a `ue-wallpaper` Spawn profile + a foreground launcher so the daemon owns the real UE PID) feeds
    the registration. **B1 + B4 touch the live lease daemon + the live wallpaper launch → to be built
    gated-inactive, routed through resource-safety + determinism review, and `[VERIFY-LIVE]`'d on the box
    before activation.**

- **B1 + B4 BUILT + TRIPLE-REVIEWED, gated-inactive (2026-06-21).** Implemented in `lease.rs` (+289):
  a pure `wallpaper_throttle_eligible` gate (heavy-lane + would-deny + min-gain + `yield_decision ==
  ThrottleAndCoexist`, unit-tested), a throttle-before-deny block in `do_acquire` mirroring the
  `warm_reclaim` choreography (peek-lock → claim under the lock → OFF-lock `spawn_blocking(apply_rung
  Floor)` → conservative poll re-measure → locked `admit` as the SOLE gate), the `RegisterWallpaper`/
  `UnregisterWallpaper` verbs, and `Inner` throttle state. **B1 = `nimbus-ue-wallpaper` Register/
  Unregister (repo-side, dormant — the live `~/.local/bin` copy is untouched until deployed at
  activation), NOT a Spawn-owned holder** (Option-X needs no PID ownership for the throttle). A
  **design** review (3 lenses) caught two BLOCKERS in the naïve token-keyed restore — and reshaped it:
    1. **Restore is an INVARIANT, not token-keyed.** The supervisor restores UE to Full whenever the
       lease is FREE for `WALLPAPER_RESTORE_FREE_TICKS` (≈3 s anti-strobe). This catches EVERY release
       path (explicit/natural-exit/TTL/peer-disconnect/preempt-eviction/denied-leak) — a preempt keeps
       UE floored (the preemptor holds the lease) by construction. Closes the stuck-at-floor cardinal sin.
    2. **Poll-don't-single-shot re-measure** (`poll_free_settled` = min of the settled tail) + the
       cardinal-sin guard (raise `free_opt` only on `Applied` AND a higher settled read) — UE sheds VRAM
       over frames, so a single read could over-admit and OOM. Closes the OOM cardinal sin.
    Plus a lock-claimed in-flight flag (serializes the two off-lock actuations) carrying a claim INSTANT
    so the supervisor self-heals a flag leaked by a dropped future (cancellation backstop). A **code**
    re-review (resource-safety + determinism + rust) verified BOTH BLOCKERS **CLOSED** in the shipped
    code, no lock held across any `await`, dormancy byte-identical until `RegisterWallpaper`; **204 tests,
    clippy clean.** Verdict GO (gated-inactive). The §3 preempt-LOG block (`lease.rs` ~1269) is now
    **legacy** for this surface (Option-X never makes UE a `Tier::Yielding` holder), kept only for a
    hypothetical owned-UE Spawn. **Remaining = the on-box activation gate (B2 compile + B5 `[VERIFY-LIVE]`,
    then deploy the B1 launcher): compile `spikes/ue-probe/throttle`, re-measure UE full/floor per the
    real tableau (set `AGENTOS_UE_FULL/FLOOR_MIB`; a full−floor below `MIN_THROTTLE_GAIN_MIB` keeps it
    inert), confirm `ApplyRung` over RC drops/restores the rung, then copy the launcher to `~/.local/bin`
    to activate.** Pre-activation polish (Low, from the review): a self-healing inflight already landed;
    the legacy §3 log's floor source could read the registration; an optional dedicated throttle-dwell knob.

- **Reuse, do not rebuild (ADR-0001 / ADR-0023 carry-forward).** The lease core + `AdoptScope`
  cgroup reclaim (the kill floor, built + verified); the brief contract + validator + locked palette
  + SemVer schema (the coherence gate); the tracked-path spline + raycast clip validator (the ride
  vehicle — pure geometry, port to UE); the `window-drag → Wind1 → wind.json` producer→sink
  (renderer-agnostic — UE consumes it like the shader did, lock-free vs. arbitration) — all carry
  forward unchanged. UE consumes the *same* `agent.json`/`wind.json` seam; we add a renderer, not a
  new producer grammar.

- **The minimal pointer added to ADR-0009.** Per repo convention (cf. ADR-0008's
  `Superseded by …` line), ADR-0009 gains a single one-line "Superseded in part by [ADR-0029] —
  §1 primary/fallback inversion for the ambient wallpaper surface; §2–§4 stand." Nothing else in
  ADR-0009 is altered or removed; it remains the record for the two-surface split, the eviction
  redesign, and the idle-byte-identical contract this ADR still obeys.

- **Recorded dissent (carried from the Phase-B council, 2026-06-19).** The design council's
  surface-strategy pass scored **fallback design "C" highest (7.6)** over native-layer "A" (3.3) and
  embed-shim "B" (4.3), because C retires the entire source-swap tx + the R1–R5 burden (under C the
  aurora shader *always* presents, so a UE kill is pure VRAM reclaim, not a wallpaper teardown). The
  **user overrode the council to Option A** on vision grounds (C demotes UE to a re-graded texture —
  the same "fake skin" objection that triggered the pivot). The dissent is preserved: if the Open §A
  probe fails, or if A's engine cost proves unjustifiable, **"C" is the standing fallback with the
  better feasibility score** and this ADR's Decision 1 would need to be revisited for that surface.

## Open questions for the human (framed)

1. **Wallpaper-layer probe — RESOLVED 2026-06-20 (passed emphatically).** UE runs as a stable
   native-Wayland wallpaper at stacking [1]; the layer is no longer a question. The boot-survival
   probe (65+ min input-less, stable, ~1.4 GB) plus rungs 1-3 close this. What's left is *which path
   to productionize* — see Q3.

2. **A vs. C — SETTLED to A (delivered, not just chosen).** Real UE, real Vulkan, a genuine separate
   surface — not a texture. Option C (UE → `dreamTex`) survives only as the documented fallback if the
   GPU-time budget ever proves unworkable. The council's feasibility worry behind C ("native layer is
   a large fork") is **refuted** — it was a localized patch / a zero-build window rule.

3. **NEW — which path to productionize, and the source-build gate.** Two working paths exist:
   **(i) keep-below KWin window rule** — zero engine changes, works on the current *Installed* UE
   build today (`spikes/ue-probe/ue_wallpaper/wallpaper_keepbelow.sh`, with an `t.MaxFPS` cap); not
   yet truly input-passthrough (wants a persistent no-focus rule for polish). **(ii) native
   `zwlr_layer_shell` BACKGROUND role** — the clean, input-less ADR design; the `LinuxWindow.cpp`
   patch + `libagentos_layershell.so` helper are written and ready, but **`~/UnrealEngine` is an
   Installed Build and cannot compile engine patches** (unlocking → UBT `RulesError`). Landing (ii)
   needs a **source build of UE 5.8**. Recommendation: ship (i) now as the usable wallpaper; schedule
   an overnight source-build of UE 5.8 to land (ii) as the production-grade, input-less version.
