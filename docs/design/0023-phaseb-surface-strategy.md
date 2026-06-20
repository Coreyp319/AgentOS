# Design-council brief — ADR-0023 Phase-B SURFACE strategy (resolving the §12.5 wayland BLOCK)

- Status: **Decided by Corey (2026-06-19) — §9 Q1 governed to Option A (hold for the live
  authoritative UE stage). The council recommended Option C; it is NOT adopted and the §7
  amendments are NOT applied. This doc stands as the recorded analysis behind the choice; C is
  retained as the fallback if A proves permanently infeasible. See "Human decision of record".**
- Date: 2026-06-19
- Facilitator: design-discourse-mediator (neutral; reconciles + decides, does not generate)
- Relates to: [ADR-0023](../adr/0023-creative-environment-pipeline.md) (creative-environment
  pipeline — amendment target), [ADR-0009](../adr/0009-dreaming-shader-primary-video-texture.md)
  (shader = permanent primary renderer; video-is-texture / `sampler2D dreamTex`; idle
  byte-identical), [ADR-0003](../adr/0003-fail-open-supervised.md) (fail-open supervised),
  [ADR-0004](../adr/0004-graphics-yield-kill-relaunch.md) (graphics yield = kill/relaunch, not
  live shedding), [ADR-0005] (apply/rollback / reversible-by-default),
  [ADR-0010](../adr/0010-vram-coordinator-overnight-batch-lane.md) (predict-before-load
  admission + one exclusive lease), [ADR-0013](../adr/0013-coordinator-ipc-trust-and-lease-lifecycle.md)
  (IPC trust / lease lifecycle).
- Parent: the Phase-B throttle-controller brief
  (`docs/design/0023-phaseb-throttle-controller-brief.md`) — this doc resolves its §12.5 BLOCK
  and §12.7 verdict-1, and feeds an amendment list back into §4 item 5 / §8 / §12.5 / §12.7.
- Inputs reconciled: three surface options (A native layer-shell UE · B external embed-shim · C
  UE off-surface into nimbus-flux), each adversarially scored across four lenses
  (`rater-feasibility`, `resource-safety-reviewer`, `rust-performance-reviewer`,
  `wayland-computeruse-reviewer`); the three Plasma-6/KWin-6.6.5/UE-5.8 surface-mechanism
  grounding findings.
- Artifacts proposed by this brief: this brief; the ADR-0023 amendment-to-an-amendment notes (§7).

---

## Human decision of record (2026-06-19)

**Corey governed §9 Q1 to Option A — "every pixel is UE, live, authoritative."** The council's
recommendation (Option C) is therefore **not adopted**; everything below stands as the recorded
analysis behind the choice, not as a build plan.

Consequences (binding until revisited):
- **The UE creative wallpaper does not ship near-term.** Option A is verified-infeasible *as a
  buildable option today* (§2, §8): native-Wayland UE is documented "unusable" by Epic (broken
  pointer input), plus a hand-rolled `zwlr_layer_shell_v1` client inside UE's `ApplicationCore`
  and a VulkanRHI swapchain retarget against a roleless `wl_surface` are net-new engine work.
- **The procedural `com.nimbus.aurora` shader remains the wallpaper** (it always was — ADR-0009;
  nothing regresses, nothing new ships).
- **The Phase-B throttle controller is PAUSED.** With no resident UE wallpaper to throttle, the
  parent brief's Layers 1–3 have nothing to act on; the §7 amendments (which baked in C's
  retirements) are **not** applied, and the parent's §12.5 BLOCK and §12.7 verdict-1 **stand**.
- **C is not deleted — it is the fallback** if A proves permanently infeasible; this analysis is
  kept intact for that contingency.

**Revisit trigger + the one cheap probe that moves A from "infeasible" to "tractable-but-large":**
the load-bearing A precondition is *undecidable at the desk* (§8) — "does native-Wayland UE run a
`-game` build as a long-lived, **input-less** wallpaper process at all?" A wallpaper takes no
pointer input, which may dodge the exact bug Epic calls "unusable." The next concrete step toward
A is a **boot-survival probe**: launch the packaged UE `-game` with `SDL_VIDEODRIVER=wayland` **on
a live session**, confirm `IsUsingWayland()` flips true, and watch a sustained run. *Pass* → A's
layer-shell + swapchain retarget becomes a bounded (large) engine effort and the ambitious vision
is alive. *Fail* → A is confirmed dead on this box and C returns to the table with real data. This
probe needs Corey's live konsole — offscreen/agent shells have no GL context (both spike READMEs).

---

## 0. The one-paragraph decision

**Adopt Option C — UE as a pure off-surface renderer feeding the existing in-process
`com.nimbus.aurora` ShaderEffect wallpaper as a texture source (`sampler2D dreamTex`), in its
SIGABRT-safe `C-fast-clip` form first.** It is the only option that **works on this box** today
(avg 7.6 vs 3.3 / 4.3), and **YES, it un-blocks build: it RETIRES G-WAYLAND-SURFACE rather than
clearing it** — UE never binds a Wayland surface, so the §12.5 BLOCK (no Application-Wallpaper
plugin, KWin doesn't reparent foreign toplevels, UE/SDL3 binds no layer-shell role) **no longer
applies to UE at all**; the present surface is a proven in-process plasmashell `WallpaperItem`,
not a layer-shell client. The one-sentence why: the kill that the whole substrate exists to
perform (SIGKILL UE to reclaim VRAM for a heavy gen) **stops being a wallpaper teardown and
becomes pure renderer-VRAM reclaim**, which collapses the brief's entire R1–R5 source-swap-tx
gate set down to "stop feeding a uniform" — reversible, idempotent, and honoring ADR-0009
verbatim. The honest cost: this **demotes UE from the live dark-ride STAGE to a degradable
texture source** (§5), and the spike must still prove the *new* buffer path (UE-offscreen-frame
→ aurora `dreamTex`) end-to-end — that re-shaped spike, not the old layer-shell one, is the
remaining build gate.

---

## 1. The fork, and why it exists (the verified §12.5 BLOCK)

ADR-0023 Phase-B assumed UE would *be its own onscreen surface* — a packaged UE 5.8 `-game`
build drawing the final wallpaper pixels directly onto a Plasma 6 background surface (option A in
disguise). The Phase-B brief's `wayland-computeruse` lane returned **BLOCK** on that foundation
(`0023-phaseb-throttle-controller-brief.md:802-870, 902-944`). The block rests on **three
facts verified on this box**, not opinions:

1. **There is no "KDE Application-Wallpaper plugin."** `/usr/share/plasma/wallpapers/` holds only
   `org.kde.{color,haenau,hunyango,image,potd,slideshow,tiled}` — all QML image/video/color
   renderers (brief §12.5). The assumed mount point does not exist.
2. **KWin does not reparent foreign Wayland toplevels into the background layer.** A UE/SDL3
   window comes up as a normal floating `xdg_toplevel` *above* the desktop, never below it. The
   "KWin reparents it to the wallpaper layer" assumption is a false X11-era carry-over (brief
   §12.5, `packaged_run.md:37-38`).
3. **UE/SDL3 binds no layer-shell role.** `Engine/.../Linux/LinuxWindow.cpp` handles only
   `xdg_toplevel` / `xdg_popup` / tooltip and sets only `SDL_WINDOW_POPUP_MENU` /
   `SDL_WINDOW_TOOLTIP` — zero `wlr_layer_shell` / background / bottom references. And native-
   Wayland UE (the only place layer-shell could exist) is documented "unusable" by Epic; UE runs
   on XWayland here, where wlr-layer-shell does not exist at all.

**The only proven external-GPU-process-as-wallpaper mechanism on this box** is the **nimbus-flux
pattern**: a thin Plasma wallpaper QML plugin (`com.nimbus.flux/main.qml:4-47` — "renders nothing
of its own," black `Rectangle` backdrop + `P5Support.DataSource(engine:'executable')`) that
spawns a standalone engine which *itself* draws on a `wlr_layer_shell` Bottom surface
(`bevy_live_wallpaper-0.4.0/.../backend.rs:374-390` — `Layer::Bottom`, `exclusive_zone -1`,
full-screen anchors). KWin 6.6.5 advertises `zwlr_layer_shell_v1` v5 + `org_kde_plasma_shell`
v8. **UE does not speak that protocol** — so the fork is forced: how do UE's pixels reach the
wallpaper layer when UE cannot own a background surface?

Three options were developed and adversarially scored:
- **A — Native layer-shell UE:** patch UE's windowing so UE itself binds a layer-shell background
  surface and presents its own Vulkan swapchain (UE owns the surface; zero copy).
- **B — External embed-shim:** UE renders offscreen → exports a DMA-BUF → a new forked-flux shim
  imports it each frame and presents it on a layer-shell Bottom surface (the shim owns the
  surface; UE owns none).
- **C — UE off-surface into nimbus-flux (§12.5 option c):** UE renders offscreen and its frames
  become the *existing* shader wallpaper's texture (`dreamTex`); UE owns no Wayland surface and
  the procedural shader always presents.

---

## 2. The three options scored (four lenses + a one-line verdict)

| Option | feasibility | resource-safety | rust-perf | wayland | **avg** | Works on box? | One-line verdict |
|---|---|---|---|---|---|---|---|
| **A — Native layer-shell UE** | 4 | 3 | 3 | 3 | **3.3** | **NO** | **Reject.** Likely-fatal precondition (native-Wayland UE = Epic "unusable"); needs hand-rolled libwayland layer-shell in ApplicationCore + a VulkanRHI swapchain retarget against a roleless `wl_surface` SDL disowned; the kill *is* the surface teardown → forces the whole unbuilt R1–R5 tx; inverts ADR-0009. Max vision fidelity, max controller cost. |
| **B — External embed-shim** | 4 | 5 | 4 | 4 | **4.3** | **NO** | **Reject.** The zero-copy crux is net-new UE VulkanRHI C++ (`vkGetMemoryFdKHR` + `DMA_BUF` + `drm_format_modifier`, none of which exist; `VulkanMemory.cpp:954` hardcodes OPAQUE_FD) gated on NVIDIA's most fragile interop; a brand-new shim presenter to review; worst VRAM; and its "what shows when UE dies?" answer converges back to C with a redundant buffer hop. |
| **C — UE off-surface into nimbus-flux** | 7.5 | 7.5 | 8 | 7.5 | **7.6** | **YES** | **Prefer.** Retires G-WAYLAND-SURFACE by construction (UE binds no Wayland surface); inherits the measured `-RenderOffscreen` regime (deletes G5); collapses the kill floor to "stop feeding a uniform" (deletes R1–R5); honors ADR-0009 verbatim. Cost: UE becomes a texture source, and `C-fast-stream`'s live decoder must be proven *inside plasmashell*, not just a qml6 harness. |

All four lenses rated A and B `reject` (works-on-this-box = false). All four rated C `prefer`
(works-on-this-box = true). **There is no lens split on the winner** — the agreement is total,
on verified-code grounds, not taste.

---

## 3. The recommended option in depth (C — UE off-surface into nimbus-flux)

### 3.0 The naming correction that must land before any code (the single most valuable catch)

The option's own seed framing — *"reuse the flux SURFACE as the texture sink"* — **is not
buildable as written, and `feasibility`, `resource-safety`, and `wayland` all independently
flagged it.** flux "renders nothing of its own" (`com.nimbus.flux/main.qml:4-11`); its engine
draws into its **own** wgpu/Vulkan swapchain on its **own** `wl_surface` (a separate KWin
client) via `WaylandWindowHandle → wgpu create_surface` and **never exports a buffer**
(`bevy_live_wallpaper-0.4.0/.../surface.rs:6,29-33`, `render.rs:143-170`). flux has **no
`QSGTexture`, no texture input, no importable buffer.** "Reuse the flux surface" is impossible.

**The surface that can actually ingest a texture is the in-process `com.nimbus.aurora`
ShaderEffect wallpaper** — a real QtQuick `ShaderEffect` (`com.nimbus.aurora/main.qml:387-562`,
`aurora.frag.qsb` + `ShaderEffectSource` bloom + a GPU `FluidLayer`). UE feeds *aurora's*
`dreamTex` seam, **not flux's swapchain.** This is more than a wording fix: it changes which
surface owns the present path (an in-process plasmashell `WallpaperItem`, **no layer-shell client
at all**), which is *why* the kill problem disappears (§4). The doc title "UE off-surface into
nimbus-flux" is retained for continuity with the scoreboard, but the buildable sink is **aurora**.

### 3.1 The exact buffer path (and the one that does NOT exist on this box)

There are **three distinct surfaces and they do not share buffers**:

- **flux** = bevy/wgpu drawing into its own Vulkan swapchain on its own `wlr_layer_shell` Bottom
  surface (a separate KWin client) — no texture input.
- **aurora** = an in-process QtQuick `ShaderEffect` wallpaper whose `dreamTex` is a `QSGTexture`
  produced *inside* the QtQuick scene graph by a Qt decoder (`VideoOutput`/`ShaderEffectSource`).
- **UE** = its own Vulkan render-target `VkImage`.

**The ONLY buffer path that works on this box (C-fast):**

```
UE -game (-RenderOffscreen) → SceneCaptureComponent2D → UTextureRenderTarget2D
  → [C-fast-clip] periodic encode of a short loop to disk          (baked, not live)
    OR
  → [C-fast-stream] raw/encoded frames over localhost/shm/named-pipe
  → Qt FFmpeg decoder (SW decode, ~13× realtime headroom, 4K@~400fps measured)
  → QSGTexture (aurora dreamTex)
  → aurora ShaderEffect applies the existing busy/warm/snag additive grade
  → in-process plasmashell WallpaperItem presents the final pixels
```

**The live zero-copy path (C-true) does NOT exist on this box** and is explicitly **out of
Phase-B scope** — it needs net-new C++ on **both** ends plus an NVIDIA-fragile negotiation:
- UE VulkanRHI exports only `VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT_KHR`
  (`VulkanMemory.cpp:954`, `VulkanTexture.cpp:463`) — which Khronos states "is not compatible
  with any native APIs"; there is **zero** `vkGetMemoryFdKHR`, `VK_EXT_external_memory_dma_buf`,
  or `VK_EXT_image_drm_format_modifier` anywhere in VulkanRHI (grep-confirmed by both
  `feasibility` and `wayland`).
- The QtQuick import side **also** does not exist: `createTextureFromNativeObject` /
  `QSGTextureProvider` are compiled-C++ scene-graph APIs needing a QQuickItem plugin, and a
  Plasma *wallpaper QML plugin cannot reach them*.
- NVIDIA dma-buf-with-modifier negotiation that KWin/Qt's import accepts is the historically
  fragile, vendor-sensitive link. Three from-scratch pieces — not an increment. Do not let a
  reviewer's "just do real zero-copy" pull C-true into the Phase-B critical path.

### 3.2 What it reuses (verbatim/extended) vs net-new

**REUSE (proven):**
- `com.nimbus.aurora` in-process ShaderEffect wallpaper — the real present surface and home of
  the reactive uniform plumbing (`com.nimbus.aurora/main.qml`).
- The dream-as-texture seam — `spikes/dream-as-texture/dream_field.frag` (`sampler2D dreamTex`,
  `texture(dreamTex,duv)` at `:39,72`) + `spikes/video-wallpaper/` (`probe.qml`, `grade.frag.qsb`,
  all-uniforms-0 == passthrough). SW-decode 4K@~400fps measured; idle-byte-identical held *in a
  harness* (see §3.3 caveat).
- The file-bridge contract (`$XDG_RUNTIME_DIR/nimbus-aurora/{agent.json,windows.json}`,
  critically-damped `window_react.rs spring()`) — decoupled, no GPU coupling.
- `wind.rs` lock-isolated agentosd→engine signal sink (atomic temp+rename,
  `idle_frame_is_byte_stable` test) — the *shape* of the new `uDreamFeed` on/off toggle.
- Pure admission core + `Reclaim::Spawned` process-group SIGKILL + Spawn `PROFILES` allowlist +
  supervisor poll loop (`lease.rs`) — reused as the UE-offscreen Spawn profile. tokio +
  zbus are already in `Cargo.toml`; the async-runtime cost is already paid.
- The already-measured `-RenderOffscreen` Phase-A numbers — **directly applicable**, because
  under C UE legitimately renders offscreen (this is what deletes gate G5).

**NET-NEW:**
1. **UE-side frame emitter** — a `-game`-embedded component that reads
   `SceneCaptureComponent2D → UTextureRenderTarget2D` each tick and either encodes a short clip
   (`C-fast-clip`) or pushes frames over IPC (`C-fast-stream`). **This is UE app/plugin C++, NOT
   an RHI patch.**
2. **A new aurora `dreamTex` reactive style** — port the spike's `grade.frag` onto the aurora
   `.qsb` pipeline pointing its `ShaderEffectSource`/`VideoOutput` at the UE frame sink, plus a
   per-style grammar table entry (only 2 of 8 styles react today). **This is build-from-spike,
   not wire-in-existing — `dreamTex`/`VideoOutput`/`MediaPlayer` appear ZERO times in shipping
   `com.nimbus.aurora/main.qml`; they live only in `spikes/`.**
3. **Throttle Layer-3 simplification** — a `wind.rs`-shaped `uDreamFeed` (on/off, critically-
   damped) uniform toggle so "stop feeding UE" is an idempotent uniform set, **not** a source-swap
   tx. No ledger, no surface-up ack, no fsync discipline.
4. **A `ue-wallpaper-offscreen` Spawn profile** in the lease allowlist (reuse `Reclaim::Spawned`
   process-group SIGKILL + supervisor poll).

### 3.3 Latency + VRAM cost vs the ~1 GB Phase-A base

**VRAM (against the ~1 GB Phase-A base = the aurora ShaderEffect surface):**
- UE term = the **measured `-RenderOffscreen` regime** (FULL ~1187–1201 MiB / FLOOR ~970–980 MiB)
  — applies *directly* because UE legitimately renders offscreen. **Gate G5 (onscreen swapchain
  re-measure) is genuinely deleted under C**, unlike A/B where every onscreen number is unmeasured.
- Feed copy: `C-fast-clip` ≈ ~0 live VRAM (a cached clip on disk; the in-graph `QSGTexture` is one
  frame, a few MB at 4K RGBA16F). `C-fast-stream` SW-decode keeps the feed **off VRAM entirely**
  (which *helps* the GPU-under-pressure premise); HW NVDEC would be ~397 MiB/stream **but HW
  decode is broken on this NVIDIA stack** (VA-API `EGL_BAD_MATCH`; `h264_cuvid`
  `CUDA_ERROR_INVALID_VALUE` headless — `spikes/video-wallpaper` README), so SW is the path.
- aurora ShaderEffect surface = cheap, scene-graph-native (single `.qsb` + bloom), marginal vs
  idle; the `dreamTex` branch reuses the existing pipeline — **no extra full-screen pass.**

**Latency:** `C-fast-clip` adds **ZERO present-path latency** (UE render fully amortized off the
present path; the cost is staleness — a baked loop is not live). `C-fast-stream` adds ~1–2 frames
(~16–33 ms) of pipeline latency + a decode/upload per frame — irrelevant for an input-less
ambient wallpaper. Crucially, **the feed cadence is decoupled from the wallpaper's display FPS**:
UE can emit at 15 fps while the shader still presents at native rate and warps `dreamTex`
continuously, so the throttle does not have to fight a present-surface swap.

**The load-bearing caveat all three of feasibility/resource-safety/wayland raised:** the
SW-decode-4K@~400fps / idle-byte-identical result is from a **bare qml6 harness**
(`spikes/video-wallpaper/probe.qml` — a top-level `Rectangle`, not a `WallpaperItem`), **NOT
inside live plasmashell.** The one place QtMultimedia video was tried *inside* the real wallpaper
scene-graph (aurora "journey") was deliberately downgraded to a static PNG `Image`
(`com.nimbus.aurora/main.qml:730-737`) with the in-code reason at `:726` — *"a QtMultimedia video
SIGABRTs plasmashell (libavcodec, NVIDIA+Wayland)"* — and an `AnimatedImage` is flagged at `:727`
as ~30%+ of a core that destabilises the scene-graph. **So `C-fast-stream`'s live in-plasmashell
decoder runs straight into a documented compositor-crash hazard on this exact box.** This is why
the recommendation is **C-fast-clip first** (baked loop via a plain `Image`, the proven journey
pattern — SIGABRT-safe today), with `C-fast-stream` gated behind a spike that proves a live
decoder survives *inside plasmashell* (§6).

---

## 4. HOW IT REWRITES PHASE-B (the most important section)

This is the deep inversion the grounding identified, and it **rewrites Phase-B in the
controller's favor.** Under the brief's assumed surface (option A), the kill target *is* the
presenting surface, so killing UE necessarily blacks the wallpaper. Under C, the present surface
is the in-process aurora shader, which **keeps presenting through every UE transition.**

### 4.1 The kill floor collapses — and §6/§12.4's whole reversibility budget is reclaimed

**Reconciling explicitly with the brief's §6 and §12.4:**

- **§6 failure row "Heavy gen exceeds even FLOOR+budget (Wan-14B cliff)"** currently reads:
  *"ADR-0005 source-swap tx commits BEFORE SIGKILL → gen runs against shader desktop; UE relaunches
  on Release"* — a whole tx that exists to avoid a "UE dead and shader not up" window. **Under C
  that window cannot occur:** the shader is already the standing surface, so SIGKILL of UE is pure
  VRAM reclaim. The row simplifies to: *"Admission denies coexist → `uDreamFeed` off (shader holds
  last frame / fades to idle) → SIGKILL UE → relaunch + re-feed on Release."* **No source-swap, no
  black-wallpaper window, no ordering constraint.**

- **§12.4 / §12.5 / §12.6 reversibility-tx gates R1–R5** exist *entirely as a consequence of option
  A* (the kill is a desktop-surface mutation):
  - **R1 (source-swap round-trip)** — there is no source-swap; the present surface never changes.
    **Retired.**
  - **R2 (surface-up-verified-BEFORE-kill ack)** — no surface comes up on kill; the shader was
    already up. **Retired.**
  - **R3 (crash-mid-swap `fsync(file)+fsync(dir)`)** — there is no swap to make crash-atomic; the
    daemon's *zero-fsync* reality (verified: no `fsync`/`sync_all`/`sync_data` in
    `crates/agentosd/src/`) stops being a blocker for this path. **Retired** (the `uDreamFeed`
    toggle inherits `wind.rs`'s atomic temp+rename, which survives a clean exit; it carries no
    durable ledger because it has nothing irreversible to recover).
  - **R4 (idempotent swap)** — "stop feeding a uniform" is idempotent by construction (a double-
    fired kill re-sets the same uniform; ADR-0009's idle is byte-identical). **Retired** as a
    *tx* gate; a `restore_is_idempotent`-style test still pins the uniform toggle.
  - **R5 (compositor-under-17 GB-load ack non-false-positive)** — there is no ack to false-positive;
    no kill is gated on "first shader frame composited" because the shader never stopped
    compositing. **Retired** — this was a *fail-CLOSED-to-black* hazard that simply ceases to exist.

  **Net: the entire R1–R5 gate set, the ADR-0005 source-swap tx, the ledger, and the daemon fsync
  discipline are RETIRED under C.** They were consequences of the surface choice, not inherent
  product costs. `reversibility-tx-reviewer` owns *confirming* this retirement (it is a gate-
  retirement decision, jointly with this mediator), but the surface mechanics that enable it
  check out across all four lenses.

### 4.2 The new actuation path

**Two knobs, and the second one deletes the worst failure mode:**

1. **Inner render-cost knob — unchanged.** RC cvars over `:30010` (`PUT ExecuteConsoleCommand`,
   the §5 path) set UE's internal cost (FULL/REDUCED/FLOOR). **Surface-independent — identical
   under A/B/C.** G-RC-ACCESS (the unauthenticated `:30010` local-code-exec hole) still gates
   ship under C exactly as under A/B; it is **not** retired by the surface choice and is owned by
   `wayland-computeruse-reviewer` (bind scoping) + `security-reviewer` (DNS-rebinding, token,
   PythonScriptPlugin code-exec).
2. **Feed knob — new, and it is the headline.** A `uDreamFeed` on/off uniform (+ a UE feed-cadence
   gate) controls whether UE's texture reaches the shader. **Stopping the feed is the ADR-0009
   idle path** (`uDreamFeed → 0`, shader holds last frame then falls to its signal-free idle
   look, idle byte-identical). It is reversible, idempotent, adds zero new irreversible acts.

**Layers 1–2 of the throttle controller (the `Tier::Yielding` admission spine + the `wind.rs`-
shaped governor) are surface-agnostic and proceed unchanged under C.** Only **Layer-3 changes:
from "a desktop-surface-mutating, fsync-gated, ack-ordered source-swap tx" to "set a uniform to
0, then SIGKILL the renderer."** The SIGKILL of UE is still the one irreversible act and is still
priority+fit-gated — but it now reclaims *a renderer's* VRAM, not *the wallpaper's* surface.

### 4.3 What stays a real cost (do not over-claim the retirement)

- **A dwell/hysteresis band is still required** at the fit boundary or the UE-texture
  appears/vanishes visibly (UE killed under pressure → idle → gen done → relaunch → next gen →
  kill). Cheaper than A (the present surface never goes away) but the UE-texture flicker is real;
  require a yield-low/restore-high gap + minimum dwell before UE relaunch.
- **The idle-byte-identical contract can regress when the `dreamTex` branch lands.** Adding a
  reactive branch to aurora risks an LSB shift in the all-uniforms-0 idle frame. A fixed-`iTime`
  idle-hash diff gate **must land the day the branch lands** (the `aurora` equivalent of
  `wind.rs`'s `idle_frame_is_byte_stable` test), or the ADR-0009 invariant breaks silently. Owners:
  `determinism-safety-reviewer` + `reversibility-tx-reviewer`.
- **Multi-monitor is N× decoders/feeds per output** (an open `spikes/video-wallpaper` item); on
  this single-4090 box it is one feed, but the coordinator must account for N before a multi-head
  ship.

---

## 5. The vision tradeoff (decided honestly)

This is the heart of the fork, and it is **a real compromise of the live-stage vision, not the
same pixels cheaper.** I will not flatten it.

- **ADR-0023's pivot vision** is UE as the **live on-screen dark-ride STAGE** — composed tableaux
  on a track, UE owning the *authoritative* final pixels in real time. **Only Option A preserves
  that faithfully** (UE's swapchain *is* the scanned-out wallpaper). All four lenses agree A buys
  maximum vision fidelity — and pays for it with a likely-fatal native-Wayland precondition, the
  full R1–R5 tx burden, and an ADR-0009 inversion.

- **Under C, UE is demoted to a texture source.** Be brutally honest about how much "live"
  survives:
  - **C-fast-clip is not live at all** — it is a baked loop. The recommended *first* form.
  - **C-fast-stream is "live-ish"** — UE's pixels pass through an encode/decode (or raw IPC)
    round-trip and are then **re-graded by the shader**. They are no longer UE-authoritative
    pixels on screen; they are *the shader's reinterpretation of a UE-derived texture.*

**The honest decision rule, stated plainly so the human can dispose:**
- **If ADR-0023's non-negotiable is "every pixel is UE, live, authoritative,"** then C does NOT
  preserve the vision, and the only option that does is A — at A's verified-infeasible-today cost.
  That is an escalation to the human (§8 of the throttle brief already routes the surface decision
  to the human), not a call I make on taste.
- **If the non-negotiable is "the ambient surface is reactive, calm, idle-byte-identical, kill-
  safe, and its content is a genuine 3-D render, not a photo-warp fake,"** then **C preserves it
  and is by far the cheaper, safer path** — and it *reconciles* the two ADR-0023 framings that only
  conflict under A. Under C the shader is **never "demoted to fallback floor"** (that pivot wording
  becomes a non-event — the shader is floor AND ceiling, UE is the enhancement on top), AND the
  "shader-over-photo = fake skin" rejection is **answered**, because `dreamTex` now carries a real
  UE render rather than a photo-warp. This is the **ADR-0009 lineage returning** (video-is-texture,
  shader-is-primary) — ADR-0023 §1/§7 explicitly keeps ADR-0009 untouched for the wallpaper and
  frames creative output as an ADR-0009 Surface-B artifact, so C is the option that does *not*
  silently drift from ADR-0009.

**My recommendation:** the second non-negotiable is the one the codebase's own non-negotiables
encode (reversible-by-default, calm-and-honest ambient mapping, idle byte-identical). On those
tie-breakers, C wins decisively. **I record A's vision claim as live dissent (§8), not as a
loser to be erased** — and I escalate the "which non-negotiable governs" question to the human
(§8 open question 1), because *that* is a vision call, not an engineering one.

---

## 6. The G-WAYLAND-SURFACE spike, re-shaped by the decision

The old G-WAYLAND-SURFACE asked "can a packaged UE `-game` present on a KWin layer-shell
background surface for hours?" **Under C that question is moot — UE never presents on a Wayland
surface.** The spike is re-shaped to prove **C's buffer path end-to-end**, smallest-first. It
must run **on a live session konsole, never from a detached/agent shell** (both spike READMEs:
offscreen has no GL context, the scene-graph render thread never starts → blank, exit 0).

**G-WAYLAND-SURFACE (C), re-shaped — four sub-probes, pass/fail each:**

1. **Loop-seam hitch (C-fast-clip, ~10-min on-session eyeball — cheapest, do first).** Does a
   baked loop re-arm with **zero black-frame hitch inside a real `WallpaperItem`** on this
   session (the journey static-`Image`/`MediaPlayer.Infinite` seek-to-0 path)? *Pass:* no visible
   gap at the loop seam. *Fail:* a 1–2 frame black flash → the clip path needs a crossfade-on-wrap
   before it ships. (`spikes/video-wallpaper` README open item.)

2. **Live-decoder-survives-plasmashell (C-fast-stream gate — the make-or-break for "live").**
   Run a live Qt FFmpeg decoder feeding `dreamTex` **inside an actual plasmashell `WallpaperItem`,
   not a qml6 harness**, for a sustained run. *Pass:* no plasmashell SIGABRT, no scene-graph
   destabilisation (the `aurora:726` hazard does not fire). *Fail:* `C-fast-stream` is dead on
   this box and **C-fast-clip is the whole shippable form** (still a clean win — the baked-clip
   delivers the reconciled vision; "live" is sacrificed).

3. **UE-frame-emitter cost (the unmeasured "UE + copy" term).** Per-frame cost of
   `SceneCaptureComponent2D → UTextureRenderTarget2D` readback/encode at 4K on the 4090, and the
   cheapest IPC (encoded clip vs raw shm ring vs localhost). *Pass:* the live UE-buffer → Qt-
   decoder feed fits a frame budget that reads as live to `motion-designer`, with a bounded VRAM/
   bandwidth term. *Fail:* the feed cost dominates → fall back to C-fast-clip. (Spikes measured
   video-FILE → shader, never a live UE frame → shader — this term is genuinely unmeasured.)

4. **Kill/relaunch hold-then-feed, no flash.** After agentosd SIGKILLs UE under VRAM pressure and
   relaunches, does aurora **hold last `dreamTex` frame / fade-to-idle with no flash**, and does
   the feed re-attach cleanly? *Pass:* no black flash; idle is byte-identical during the gap; the
   feed re-arms. *Fail:* the hold/fade needs authoring before the kill path is wired. (The ~800 ms
   flicker figure is **flux-specific**; the aurora-holds-`dreamTex` behavior is unmeasured.)

**Overall gate (binding):** C may enter build for the substrate loop (Layers 1–2 + the
`uDreamFeed` Layer-3, behind a flag) **once sub-probe 1 passes** (the clip path is proven
hitch-free in a real `WallpaperItem`) and the **idle-hash diff gate is wired** (§4.3).
`C-fast-stream` (the "live" form) may **not** ship until sub-probe 2 passes. C-true stays out of
scope. **This re-shaped spike — not the retired layer-shell one — is the remaining build gate the
Status line names.**

---

## 7. What changes in the throttle brief (the amendment list)

Short, owner-tagged amendments back into `docs/design/0023-phaseb-throttle-controller-brief.md`:

- **§4 item 5 — REWRITE.** "The KDE Application-Wallpaper Wayland surface wiring + the ADR-0005
  UE↔shader source-swap tx" is **deleted as a net-new item.** Replace with: "a new aurora
  `dreamTex` reactive style + a UE off-surface frame emitter + a `uDreamFeed` uniform toggle." The
  UE↔shader **source-swap tx is removed from NET-NEW entirely** (it was an option-A artifact).

- **§5 — KEEP the RC actuation path unchanged** (the inner render-cost knob is surface-independent).
  Add a note that the SHADER-FALLBACK rung row is no longer a "source-swap tx → SIGKILL UE →
  procedural shader" — it becomes "`uDreamFeed` off → SIGKILL UE → shader idles; relaunch + re-feed
  on Release." **G-RC-ACCESS is retained verbatim** — the surface choice does not close the
  `:30010` hole.

- **§6 — AMEND the failure table.** The "Heavy gen exceeds even FLOOR+budget" and the bad-cvar
  rows lose their source-swap/black-wallpaper machinery (see §4.1 here). Add the dwell/hysteresis
  requirement at the fit boundary and the idle-hash diff gate. The calm/ambient §6 clauses (eased
  rung transition, FLOOR mood, stale/blind tell, reduce-motion encoding) **survive unchanged** —
  they are UE-render-cost and cockpit concerns, orthogonal to surface ownership.

- **§8 / §9 — AMEND the gates.** **G5 (onscreen footprint re-measure) is DELETED** (UE legitimately
  renders offscreen under C; the Phase-A `-RenderOffscreen` numbers are the shipped numbers).
  **G-WAYLAND-SURFACE is RE-SHAPED** per §6 here (UE-frame → `dreamTex` end-to-end, four sub-probes).
  **R1–R5 are RETIRED** (no source-swap; `reversibility-tx-reviewer` confirms the retirement). G1–G4
  (richer-scene re-measure, live-RC latency, coexistence, frame-time) and **G6 (RC auto-start)**
  are unchanged — they are render-cost/actuation gates, not surface gates.

- **§12.5 — RESOLVE the BLOCK.** Record that the `wayland-computeruse-reviewer` BLOCK named option
  (c) — "abandon native-UE-as-wallpaper and treat UE as an off-surface renderer feeding the
  existing flux/shader layer" — as one of the three concrete options; **this brief selects exactly
  that (Option C), with the correction that the texture sink is the in-process `aurora` ShaderEffect,
  not the flux swapchain.** The BLOCK is **resolved by retirement, not by clearing**: UE binds no
  Wayland surface, so the two false-assumption foundation the BLOCK rested on no longer applies.

- **§12.7 — UPDATE verdict 1.** "`wayland-computeruse-reviewer` returns BLOCK; build gated on
  G-WAYLAND-SURFACE before any lease-core surgery" is amended to: "the surface fork is resolved to
  Option C, which **retires** the original G-WAYLAND-SURFACE; build of the substrate loop (Layers
  1–2 + `uDreamFeed` Layer-3) proceeds once the re-shaped G-WAYLAND-SURFACE sub-probe 1 passes +
  the idle-hash gate is wired. The reversibility-tx R1–R5 gate set is retired with the source-swap
  it gated. G-RC-ACCESS and the four user-facing-ship gates (a11y, sound, privacy) **remain**."
  The four-lane *ship* gates and the BLOCK-on-the-old-foundation are **not** flattened — they are
  re-pointed at the surviving surface.

---

## 8. Recorded dissent + what DOES-NOT-WORK on this box (stated plainly)

**DOES-NOT-WORK-on-this-box (verified, stated without softening):**
- **Option A (Native layer-shell UE) does NOT work on this box.** All four lenses: `works_on_this
  _box = false`. It is gated behind a precondition Epic itself documents as "unusable" (native-
  Wayland UE / broken mouse input), requires a hand-rolled libwayland layer-shell client inside
  ApplicationCore against a roleless `wl_surface` SDL disowned, and a VulkanRHI swapchain retarget
  through `SDL_Vulkan_CreateSurface` (`VulkanLinuxPlatform.cpp:363`) that assumes a shell-managed
  window. It is **not** buildable as a config/packaged option today.
- **Option B (External embed-shim) does NOT work on this box.** All four lenses: `works_on_this
  _box = false`. Its zero-copy crux is net-new UE VulkanRHI DMA-BUF export that does not exist
  (`VulkanMemory.cpp:954` OPAQUE_FD-only; zero `vkGetMemoryFdKHR`), gated on NVIDIA's most fragile
  interop, plus a brand-new shim presenter and a fallback story that converges back to C.
- **Option C-true (live zero-copy UE `VkImage` → DMA-BUF → `QSGTexture`) does NOT work on this
  box** either — three from-scratch C++ pieces on both ends + NVIDIA modifier negotiation. Out of
  Phase-B scope. **Only C-fast (the C-fast-clip form first, C-fast-stream after a plasmashell-
  survival spike) works.**

**Recorded dissent (never erased):**
- **The live-stage vision dissents from C.** If ADR-0023's non-negotiable is genuinely "every
  pixel is UE, live, authoritative on the wallpaper," then C is a real compromise — it makes UE a
  re-graded texture, not the authoritative compositor. The only option that honors that vision is
  A, which is verified-infeasible-today. **I do not flatten this into "C is the same pixels
  cheaper" — it is not** (§5). This dissent is owned by `art-director` (the vision lane) and is
  escalated to the human as open question 1. The four engineering lenses are unanimous *that C is
  the only buildable option*; they are explicitly **not** the owner of the vision-fidelity call.
- **`resource-safety-reviewer`'s standing caveat survives C:** C does **not** make UE *fit*
  alongside a 17 GB gen — ~1 GB UE + ~17 GB gen + ~2.5 GB irreducible user-app graphics +
  compositor headroom tops 24 GB. C makes the **eviction safe** (no surface loss), not the
  coexistence live. Coexistence with a true VRAM-cliff gen remains **kill-to-(idle-shader)**, not
  live throttle, until G1's richer-scene re-measure says otherwise. State this honestly; do not
  let "C deletes the black-wallpaper problem" be heard as "C lets UE coexist with Wan-14B."

**Missing voices (named, not filled — a synthesis that silently fills an empty lane is the most
dangerous false consensus):**
- **`art-director`** must own the §5 vision-fidelity call (live-stage vs reconciled-texture) — it
  is a taste/vision lane I do not own and did not fill; I escalate it.
- **`motion-designer`** must judge whether C-fast-stream "reads as live, not laggy" (sub-probe 3
  pass/fail) and whether the C-fast-clip loop-wrap reads calm.
- **`ambient-embodiment-reviewer`** owns the UE-frame/VRAM budget for the live UE-buffer → shader
  feed (the unmeasured "UE + copy" term).
- **`responsible-ai-privacy-skeptic`** — the desktop→engine signal posture for the new aurora
  `dreamTex` consumer (geometry/pressure only, never window content) is unchanged-but-unconsulted
  for this surface; PRIVACY-GATE-1/2/3 carry over.

---

## 9. Open questions for the human (options + a recommendation)

1. **Which ADR-0023 non-negotiable governs the surface — live-UE-stage, or reactive-calm-kill-safe
   ambient?** This is the vision call that decides A-vs-C.
   - (a) **"Every pixel is UE, live, authoritative"** → only Option A satisfies it, at the cost of
     a likely-fatal native-Wayland precondition, the full R1–R5 tx burden, an ADR-0009 inversion,
     and an unvalidated onscreen footprint — and it is **verified-infeasible-as-a-buildable-option
     today.**
   - (b) **"Reactive, calm, idle-byte-identical, kill-safe, genuine-3D-content (not a photo-warp
     fake)"** → Option C satisfies it, works on this box, retires the surface BLOCK and the R1–R5
     gates, and honors ADR-0009 verbatim — at the cost of UE being a re-graded texture, not the
     authoritative compositor.
   - **Recommendation: (b) / Option C.** The non-negotiables the repo encodes (reversible-by-
     default, calm-and-honest ambient mapping, idle byte-identical, performant/yield-aware) are
     tie-breakers, and they point at C unanimously; A's vision win loses on every one of them. C
     also *reconciles* the two ADR-0023 framings rather than forcing the ADR-0009 inversion the
     codebase flags as unintended. Take A only if the live-authoritative-stage is a hard product
     non-negotiable you are willing to fund as a from-source UE engine effort gated on a precondition
     Epic calls "unusable."

2. **C-fast-clip (baked, ships now) or hold for C-fast-stream (live-ish, needs the plasmashell-
   survival spike)?**
   - (a) **clip first** — SIGABRT-safe today, delivers the reconciled vision, ships once sub-probe
     1 passes.
   - (b) **wait for stream** — adds "live-ish" motion but only after sub-probe 2 proves a live
     decoder survives inside plasmashell (the `aurora:726` SIGABRT hazard is real on this box).
   - **Recommendation: ship (a) now, promote to (b) only after the plasmashell-survival spike
     (sub-probe 2) returns green** — matches ADR-0023's measure-before-architecture discipline and
     gives you a working, kill-safe creative wallpaper immediately without risking a compositor
     SIGABRT under a 17 GB gen.

3. **Confirm the R1–R5 / source-swap-tx retirement is acceptable, or keep the tx as defense-in-
   depth?** Under C there is no surface mutation to make crash-atomic, so R1–R5 and the unbuilt
   ADR-0005 source-swap tx are retired (§4.1).
   - (a) **retire them** (recommended) — they were option-A consequences; retiring them removes the
     single largest net-new safety surface (the daemon has zero fsync; building a tx engine purely
     to make A's kill non-harmful is unjustified once the kill no longer touches the surface).
   - (b) keep a minimal tx anyway — only justified if a future option-A path is still on the table.
   - **Recommendation: (a), ratified by `reversibility-tx-reviewer`** — the retirement is a direct
     consequence of the surface choice, not a corner cut; the `uDreamFeed` idempotent uniform +
     the idle-hash diff gate are the right, far smaller, reversibility surface for C.

---

*The full ADR-0023 amendment text is for code + the human to dispose; this brief proposes the
surface resolution and the gate re-shaping, it does not ratify.*
