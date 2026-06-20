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
    UE runs **live-windowed** on this Wayland box. A retired risk still has an unwritten
    implementation — the `Tier::Yielding` tier, the throttle governor, the keyhole control
    back-channel, and the dark-ride sequencer are all **unbuilt in `crates/agentosd`**.
  - **OPEN / NOT ratified — do not overclaim:**
    - **(A) The true Wayland WALLPAPER LAYER is UNBUILT and is a large engine/compositor effort.**
      Verified on-box: there is **no "Application-Wallpaper" Plasma plugin** (only stock
      `org.kde.*`), KWin does not reparent foreign toplevels, and UE/SDL3 binds no layer-shell role
      — so **UE cannot be a *native* Wayland wallpaper** as a window-to-background. Live-windowed
      proves UE-runs-on-Wayland, *not* the layer. The user **governed the vision call to Option A**
      ("every pixel is UE, live, authoritative"), which requires a hand-rolled layer-shell host +
      VulkanRHI swapchain retarget — bounded but large. **The one cheap gating probe before any of
      A is built:** boot-test whether a native-Wayland UE `-game` build runs as a long-lived,
      *input-less* wallpaper proc (a wallpaper takes no pointer input → may dodge the bug Epic calls
      "unusable"); it **must** run on the user's live console (offscreen/agent shells have no GL
      context). Pass → A is a bounded engine effort; Fail → A is dead on this box and fallback "C"
      returns with data. Routed to `wayland-computeruse-reviewer` + `design-technologist`.
    - **(B) The Remote Control server (`:30010`) is an unauthenticated local-code-exec hole** and
      must be locked down before the throttle channel ships. Routed to `security-reviewer`.
    - **(C) `capture_shot` offscreen self-verify is OVEREXPOSED** (SceneCapture2D self-auto-exposes,
      not yet exposure-matched to the `-game` runtime truth); and **motion auto-play in `-game` is
      pending live confirmation.** These are spike-verification gaps, not design decisions.

- **The Phase-B throttle controller is currently PAUSED, by consequence.** Because Option A (the
  authoritative UE wallpaper layer) is not yet built, there is **nothing to throttle yet**; the
  procedural aurora shader **remains the live wallpaper today**. `Tier::Yielding` and the governor
  are *designed* and wait on the Open §A probe. This is the honest "proposal-of-a-proposal" state:
  the human disposes on a longer time-horizon than a normal feature.

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

1. **Wallpaper-layer probe — go/no-go on the one cheap test (Open §A).** Recommendation: **run the
   input-less native-Wayland `-game` wallpaper-proc boot-test on your live console first** (cost:
   ~hours, no architecture written). It is the single number that moves Option A from
   "infeasible-today" to "tractable-but-large," and it must precede building `Tier::Yielding`.
   *Cost of skipping:* writing the throttle architecture against an unproven layer — the exact
   "ADR says so but no code under it" trap.

2. **If the probe fails: A vs. C.** Option A (authoritative UE, your vision call) vs. Option C (UE
   off-surface → aurora `dreamTex`, the council's 7.6 pick, retires the tx burden). Recommendation:
   **hold for A** per your governance call, with **C as the explicit, documented fallback** — do not
   silently let the higher feasibility score flip the vision without your re-disposition.
