# AgentOS — Vision

> The north-star for AgentOS. Architecture decisions are in [`docs/adr/`](adr/); this is
> the narrative that ties them — and the embodiment vision — together.

## What AgentOS is

AgentOS is **not** a new OS, a distro, or an agent orchestrator. The orchestrator already
exists on this machine: **Hermes Agent** (`~/.hermes`, Nous Research) — gateway, kanban task
engine, delegation, cron, skills, memory, local Ollama. The desktop already exists: **CachyOS
+ the Nimbus pack**. AgentOS is the small **resource + safety substrate** they both stand on,
plus the **ingrained UI** that turns "Hermes happens to run here" into "this machine *is* an
agentic desktop." (See [ADR-0001](adr/0001-substrate-not-orchestrator.md).)

```
        Hermes (brain)  ──inference──▶  agentosd  ──▶  Ollama / GPU
        + plugin (priority, lease)        │ (gateway · VRAM arbiter · apply/rollback tx)
                                          │
   faces:  attention overlay · aurora wallpaper · swaync · tray glyph · Spotlight
        Nimbus desktop = a consumer of agentosd; Hermes stays the brain.
```

## The substrate — `agentosd` (v1, the foundation)

A small Rust daemon. The genuinely new, build-worthy floor under everything else:

- **Enforcing inference gateway over a *configured* Ollama** — a thin transparent OpenAI/Ollama
  proxy adds priority + metrics + the VRAM-yield trigger; Ollama's own env config does residency
  and queueing. Not LiteLLM, not a custom scheduler. ([ADR-0002](adr/0002-thin-gateway-configure-ollama.md))
- **VRAM arbitration** — the real, under-served problem on one 24GB GPU. Live data showed the
  *primary* lever is **model-side** (fit/swap the model, evict idle models); wallpaper-RT
  kill/relaunch is a conditional secondary. ([ADR-0004](adr/0004-graphics-yield-kill-relaunch.md))
- **Apply/rollback transaction API** — hybrid file-backup + inverse ops, ledger, earned-autonomy,
  Timeshift backstop. The deterministic floor the inference path fails *open* against.
  ([ADR-0005](adr/0005-apply-rollback-transaction.md), [ADR-0003](adr/0003-fail-open-supervised.md))
- **Hermes plugin, no fork** — priority tag + GPU lease via supported middleware. ([ADR-0006](adr/0006-hermes-plugin-no-fork.md))

*Status:* `crates/agentosd` runs a read-only VRAM monitor (per-process NVML + fit-based verdict).
Next: the arbitration decision engine (dry-run), then the gateway shim.

## Computer-use across the OS (v2)

Hermes already ships the entire `computer_use` tool layer (capture/click/type, approval gates,
vision routing) — but its backend is **macOS-only**, behind a `ComputerUseBackend` interface
built for "future Linux/Windows." So AgentOS does **not** rebuild a computer-use agent; it wires
a Linux/Wayland backend. On KDE Plasma 6 the sanctioned stack is present: **libei** (input),
**xdg-desktop-portal-kde** (capture), **AT-SPI2** (semantic tree). Plan: wire the KDE-native
**`kwin-mcp`** (EIS/libei + AT-SPI2, virtual-sandbox *and* live modes) first; build a native Rust
backend later only if needed. Modality is **semantic-first** (AT-SPI), vision/SOM as fallback.

## Inline rules (v2/v3)

Govern any item with a rule = **subject + trigger + action + scope + autonomy**:

- **Pointing binds the subject** ("this rule is *about* this thing"); trigger (incl. cron),
  action, scope, autonomy are separate facets — so a "hard cron rule" and a "point-at-this-
  notification rule" are the same object.
- **Author per-item** (focus item → summon → frosted card blooms; NL line → Hermes fills
  editable `WHEN/DO/SCOPE/MODE` chips; scope is a *slider*, not a wizard).
- **Manage in a Rules panel** (the audit/edit/disable surface).
- **Test live** — dry-run plays the rule as a translucent **ghost**; Approve once (reversible
  tx) or Trust (earned autonomy).
- **Reuse:** authoring rides the attention overlay; dispatch goes to Hermes cron/hooks/kanban +
  agentosd tx/state-triggers.

## The ambient agentic desktop (embodiment)

The agent has **no home app — it *is* the environment** (ambient-first). Awareness is ambient;
interaction is summoned.

- **Notifications = the nervous system** (reuse swaync): the agent speaks/acts through native
  actionable toasts; every notification carries a `⊙ rule this` affordance; approvals + the
  trust ramp live here. The agent is also a notification *concierge* (rules triage/summarize/
  batch/suppress the stream).
- **Orchestration = ambient, not a dashboard:** the wallpaper carries the mood, a summonable
  tray "fleet" popover carries the overview (tasks, queue, GPU lease), Spotlight is the fleet
  command line, and the attention overlay is the spatial face when an agent touches the GUI.
- **One visual language everywhere:** *accent = the agent · glass = its surfaces · ghost+shimmer
  = a proposal/simulation · bloom = live activity · warm = needs-you.*

---

## Reactive surfaces - the wallpaper as the agent's mood

The wallpaper is ambient mood, never the message: it carries how the fleet *feels* while the tray glyph stays the legible fact and the always-on fallback. Every style keeps its single native accent and reserves warmth for one state only -- `needs_you` -- so the rare warm breath always means "your attention is wanted." Everything is tuned to pass the 100th-viewing test: low-amplitude, slow, ignorable until you choose to read it.

### The shared reactive grammar

A new **agent.json bridge** -- a direct clone of the existing window/audio bridges -- maps fleet state to `$XDG_RUNTIME_DIR/nimbus-aurora/agent.json`. The QML aurora polls and low-passes it exactly like `uMusicReact`/`uActiveMove`; the bevy nimbus-flux scenes poll it like `window_react.rs` polls `windows.json`, smoothing through the same critically-damped spring so transitions ease in over the ambient 2-20s timescale and never snap.

Every wallpaper, whatever its vocabulary, follows the same abstract state-to-primitive mapping:

- **idle** -- the resting state the others depart from. Agent drives at 0; visually identical to the wallpaper at rest.
- **working** -- encoded *only* through motion-rate + accent-intensity + bloom, scaled by fleet busyness. No new hue: "the same scene, running harder." Eased over a few seconds, capped low.
- **needs_you** -- the single deliberate warmth exception: a slow (~4-10s) warm breath on the shared `accentWarm`, localized to a focal point so foreground legibility is untouched. The only state that touches warmth.
- **acting** -- minimal by design; the spatial-attention overlay owns this. The wallpaper offers at most a faint, brief cool/focus cue and never adds competing motion.
- **snag** -- a calm, non-alarming cue: flow slows below idle and the scene slightly cools/dims, reading as "stopped, waiting." Never red, never flashing.

**Reduce-motion fallback:** all agent-driven *motion* terms drop to zero and the autonomous drift freezes near-static. State is then carried by static, low-amplitude *tone* only (a steady cool floor for working, a held non-breathing warm lobe for needs_you, a steady desaturation for snag, nothing for acting). Because the tray glyph stays the legible fallback, full-calm mode can gate every agent tone term to zero with no information loss.

### Per-wallpaper reactivity

| Style | Look | Idle | Working | Needs-you | Snag | Feasibility |
|---|---|---|---|---|---|---|
| **Flow** (0) | Domain-warped fbm ribbons advected along a diagonal current | Calmest slow drift, no agent term | Current quickens, ribbons broaden + faint cool rise (`flowAmt`+`D`, shade floor, cool `light`) | Slow warm breath from lower-centre on the brightest crests (`accentWarm`) | Flow slows below idle, ribbons desaturate via `uIntensity` | medium |
| **Hills** (1) | 5 receding ridgelines, breathing depth-of-field | ~40% slower drift, base focus sway | Parallax speeds, focus breathes faster/wider, in-focus shimmer lifts | Warm dawn glow gathers low behind the far ridges | Air thickens (more haze), drift slows, one cool dim breath | easy |
| **Silk** (2) | Aurora curtains streaming sideways | Curtains nearly still, bare drift | Bands stream faster, folds undulate, tops glow cooler/brighter | One curtain near focus takes a warm crest, slow breath | Stream stalls, sheet dims, one slow cool ripple | easy |
| **Caustics** (3) | Thin web of bright water-light veins | Narrow, well-separated veins, slowest drift | Veins travel faster, web densifies (wider junctions) | Warm bloom breathes up through bright junctions | Current slows, veins thin and still, slight dim | easy |
| **Ink** (4) | Pigment plumes rising through cool water | Plumes thin, near-suspended | Plumes rise faster + fuller, cool bloom on bright pigment | Warm bloom suffuses one central rising plume | Current stalls, pigment disperses/desaturates | easy |
| **Laserwave** (5) | Neon synthwave horizon + grid | Slowest grid scroll, dim sun | Grid marches faster, lines firm, halo brightens | Banded sun + horizon haze lean warm amber, breathing | Grid scroll stalls, lines dim/desaturate (no red) | medium |
| **Vaporwave** (6) | Raymarched pastel colonnade + sun | Slowest camera dolly, gentle sphere bob | Dolly quickens, aisle deepens, cool sheen lifts | Vanishing-point sun swells + warms, warm pool forward | Dolly damps, pastel distance haze thickens | medium |
| **Cyberpunk** (7) | Tron neon data-grid flythrough | Dim, dolly crawls, sparse traffic | Faster dolly, denser/faster packets + traffic, brighter grid | Horizon glow warms to amber, slow warm swell | Dolly + traffic stall for one beat, magenta flecks | medium |
| **Liquid** (8) | GPU Eulerian fluid, glowing ink | Slow emitter orbit, dim quiescent dye | Emitters orbit faster, curl strengthens, ink brighter | Warm dye blooms as if one emitter turned warm | Energy ebbs, ink stills + dims slightly | medium |
| **flux Cyberpunk** (9) | Bevy BR2049 Night City | Traffic thins/slows, baseline spin/bloom | Light-trails faster/denser, core+rings spin up, bloom lifts | Core heart swells, grade warms toward amber | Traffic + core spin stall mid-flow (no red) | easy |
| **flux Hexen** (10) | Bevy gothic torch-lit nave | Torches low/steady, slowest dolly | Flicker + dolly quicken, bloom widens, hotter torches | Far shrine (bust + candles) swells + warms, breathing | Torches gutter + cool, dim and hold | medium |
| **flux Journey** (11) | Bevy torch-lit corridor cruise | Torches held steady, cruise at floor, blue-quiet | Cruise speeds, flames livelier/brighter, more bloom | God-ray shaft warms + widens, torches pulse in-phase | Cruise near-stops, torches gutter, fog thickens | medium |
| **flux Fluid** (12) | Bevy stable-fluids (ink/mercury/water) | Stock no-input drift, do nothing extra | Faster advection, swirls persist longer, stronger emitters | Salmon `palette3` mixes into emitter dye, breathing | Sim slows, emitters quiet, dye fades faster | medium |

For **acting**, every style is deliberately minimal and defers to the spatial-attention overlay: Flow/Laserwave/Caustics give a faint cool focus pool, Hills/Vaporwave nudge depth-of-field/sphere toward the action, Silk sharpens one fold at the focus column, Ink/flux-Fluid drop a quiet droplet at the action point (if coordinates are supplied), and the bevy scenes simply ease their idle orbit/sway toward still.

### Build notes

The state-to-visual mapping is cheap everywhere because each style already exposes the levers it needs; the real work is the shared bridge plus per-family plumbing.

**2D procedural (Flow, Hills, Silk, Caustics, Ink -- aurora.frag styles 0-4).** Add ~4-5 std140 uniforms to the buf block (`uAgentState` int + `uAgentBusy`/`Warm`/`Snag`/`Act` floats), threaded into `baseLook` and the shared `light` block; the music packet is the exact template (eased, master-gated scalars). All five states reuse existing knobs -- `flowAmt`/`D` for pace, the `shade` threshold floor for broadening, the shared additive `light` (`accentWarm`/`accentCool` under the hue-preserving highlight guard) for glow, and the `uIntensity`/luma path for desaturation. Hills/Silk/Caustics/Ink are **easy**; Flow is **medium** only because it touches the most terms. Watch-items: keep agent contributions capped low and behind the highlight guard so working + loud music don't compound into a white blowout, and keep `needs_you` the sole warm source.

**aurora-3D (Laserwave, Vaporwave, Cyberpunk -- styles 5-7).** Same uniform pattern but **medium**, since edits must stay in std140-layout sync across `aurora.frag` *and* `react.frag`, plus a qsb recompile. Hooks land on existing terms: grid `drive` / camera dolly `t` for motion, sun `halo`/`sunCol` and horizon `hz` for the warm `needs_you`, the `rip` ripple sum (and react.frag `.b` seed) for acting cues, `shade` for accent. Two subtleties: drive the dolly *multiplier* through the eased value (a spring), or column/grid phase will visibly scrub when busyness ramps; and encode the precedence rules (needs_you warmth > working cool, snag-damp > working-boost) in the bridge, not the shader.

**Liquid fluid (style 8).** **Medium**, spanning four files (`fluid_velocity`/`dye`/`display.frag` + `FluidLayer.qml` + a new poller in `main.qml`) with byte-exact uniform-block layout across QML and GLSL and a qsb recompile. The agent feed claims the two ambient generators no other input drives -- the orbiting emitters and the curlNoise current -- plus a global `uIntensity` trim and the warm `c4` stop for needs_you; it enters via the direct-uniform path the music/window feeds already use (Liquid does not sample reactTex), the lowest-risk integration point.

**nimbus-flux engine scenes (styles 9-12).** No shader edits -- pure CPU-side bevy param modulation each Update tick. Add an `AgentReact` resource + `poll_agent` system cloned from `window_react.rs` (mtime-gated read of agent.json, low-pass + critically-damped spring), then multiply existing per-frame params: Traffic.speed / spin / Bloom / ColorGrading.temperature / core emissive (Cyberpunk, **easy**); torch flicker envelope / dolly / shrine keys (Hexen, **medium**); cruise speed / flicker / god-ray DistanceFog tint (Journey, **medium**); FluidConfig `dt`/dissipations/`force_scale`/`palette3` (Fluid, **medium**). Use a low spring omega (~1-2) so the ambient timescale is respected -- do **not** reuse the drag-responsive window omega (~7). Caveats: keep spring omegas low and grade/material mutations smoothed to avoid popping; Hexen must mark the bust + candle keys with a distinct component so needs_you/snag target them apart from wall torches, and its RT (Solari) path floods GI from `torch.base`, so validate gains against an RT capture; flux-Fluid's needs_you warm-mix needs one free uniform lane plus ~3 lines in `splat_dye`, and positional `acting` for Ink/flux-Fluid only works if agent.json carries action coordinates (otherwise correctly a no-op).

---

## Sequencing

```
v1  agentosd substrate     ── FOUNDATION ── monitor ✓ → decision engine → gateway
v2  computer-use (kwin-mcp + attention overlay)   ┐ ride on the substrate
v2  ambient embodiment (agent.json bridge + reactive surfaces + swaync + tray)  ┘
v3  inline rules (authoring card + Rules panel + ghost test)
```

## Principles

- **Don't reinvent.** Hermes is the orchestrator; Ollama schedules; the gpu-effects toolkit
  renders. Build only the gaps (VRAM arbitration, the Wayland backend, the rule model, the
  agent.json bridge).
- **Deference.** The agent is ambient until it needs you. Effects serve content; pass the
  100th-viewing test; one accent; warmth means *needs-you*.
- **Reversible & deterministic.** Model proposes, code disposes. Everything an agent changes
  goes through the apply/rollback tx; GUI actions (which can't) get the visible ghost + approval.
- **Never gate function on an effect.** The tray glyph is always the legible fallback for the
  ambient signal; reduce-motion / reduce-transparency are first-class.
