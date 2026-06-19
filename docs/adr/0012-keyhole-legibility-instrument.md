# ADR-0012: The keyhole — a read-only resource/arbitration instrument (not a manage console)

- Status: Proposed (design-council approved direction 2026-06-16, aggregate 8.1/10 uncapped;
  feasibility spike VIABLE 2026-06-16, `spikes/keyhole/`;
  see `docs/design/keyhole-instrument.md`, `keyhole-scorecard.md`, `keyhole-positioning.md`)
- Date: 2026-06-16
- Relates to: ADR-0001 (substrate, not orchestrator), ADR-0002 (thin gateway / proxy →
  tokens/sec), ADR-0004 (graphics yield: the preempt event the keyhole narrates), ADR-0005
  (apply/rollback tx → the deferred revert), ADR-0006 (Hermes plugin → the deferred approve),
  ADR-0010 (the VRAM lease this surface makes visible). Extends the `feed.rs` ambient producer.

## Context

The substrate's headline work — the VRAM coordinator / preemptible lease (ADR-0010) — is
**invisible**: it only `println!`s. The only structured output today is `agent.json` = four
scalars (`state/busy/warm/snag`) driving the reactive-wallpaper *mood*; `monitor`'s NVML/model
data is ephemeral stdout; `lease` state is reachable only via `busctl`; tokens/sec is measured
nowhere. So a user can see the agent is *busy* but not *what it is doing, whether it is OK, or who
holds the GPU and why*. We need a legible, taskbar-reachable "keyhole" into AgentOS — to **view**
ongoing work and, later, to **steer** it.

Two facts fix its shape:

1. **Hermes already ships the board.** `hermes dashboard` serves a full React/Vite kanban at
   `http://127.0.0.1:9119`. Rebuilding it in agentosd is the ADR-0001 anti-pattern. The board is a
   **link-out**, not a rebuild.
2. **The reassuring null is a live honesty bug.** `feed.rs` `read_fleet(...).unwrap_or_default()`
   collapses a dead gateway / unreadable kanban / stuck WAL into calm-`idle` — the desktop looks
   calmest exactly when it is most broken.

## Decision

1. **The keyhole is a read-only resource/arbitration instrument, not a manage console.** Its body
   surfaces the slice only agentosd owns: VRAM pressure, model residency, and — **led, not as one
   row among gauges** — the **GPU lease/arbitration state**: who holds the lease, at what tier, and
   the preempt event that resolved contention (e.g. *"wallpaper yielded ~1.5 GB → model loaded"*,
   ADR-0004). The kanban board is a **link-out** to Hermes' `:9119` (gateway-gated).

2. **A new read-only `keyhole.json` producer** (a sibling agentosd mode), separate from — never a
   widening of — the 4-scalar `agent.json` wallpaper contract. Atomic temp+rename, edge-driven,
   **adaptive cadence** (2s idle → 5–10s under load / slow NVML → skip-on-stale). It carries a
   versioned `schema` field and is pinned by a serde round-trip test.

3. **The observer boundary is load-bearing (fail-open, ADR-0003).** The UI consumes JSON files
   only; it **never** opens NVML or calls the lease D-Bus per-render — `Status` locks the same
   `Mutex<Inner>` that gates preemption, and **a render must never delay a SIGKILL**. The lease
   daemon **pushes** its state to a sibling `lease.json` mirror on transition (which the `keyhole`
   producer merges), fire-and-forget **off the lock** — snapshot taken under the lock, atomic
   temp+rename written after it is dropped.
   The consumer reads the file via a `Timer`-driven **`Plasma5Support.DataSource`** (re-issued
   `cat` per tick — async, non-overlapping, skip-on-stale for free), **not `XMLHttpRequest`**: the
   spike proved that under plasmashell 6.6.5 / Qt 6.11 XHR on `file://` is fully disabled (returns
   empty; plasmashell sets no `QML_XHR_ALLOW_FILE_READ`), so the XHR path reads nothing in a real
   plasmoid.

4. **A first-class `UNKNOWN` state**, distinct from `idle` and from a real `0`
   (*"Status unavailable — can't reach Hermes"*), driven by `gateway_state` + feed-freshness mtime.
   Polite while `degraded`/restarting; assertive only when sustained. This is the honesty fix for
   the reassuring null.

5. **v1 is strictly read-only + link-out + the taskbar embodiment.** It is the pull-only,
   closed-by-default **foveal zoom** from the wallpaper mood (tray glyph → peek → full); the glyph
   vanishes at true idle (byte-identical-to-baseline). It may surface itself only on a `needs_you`
   transition via a swaync toast, never auto-popping.

6. **All write/steer actions are deferred behind real dependencies** (model proposes, code disposes;
   reversible-by-default): approve `needs_you` / pause / cancel → **P2**, gated on a *confirmed*
   Hermes write-API (v1 adds no write-path of its own); live tokens/sec → **P2**, via the ADR-0002
   proxy (never synthesized — `UNKNOWN` until then); revert "what the agent changed" → **P3**, needs
   the ADR-0005 tx + ledger.

7. **Calm/honest/accessible by construction.** Native KDE blurred popup, no custom shader (no GPU
   competition with inference); quiet-dark, density-grows-with-load. `state` is the single source
   of truth for every affordance; redundant shape+text+color (never color-only) on a
   contrast-locked token; `snag` = "Paused — waiting" (cool/dim, **never red**); one warm element
   max (`needs_you`); `aria-live` on transitions only; reduced-motion stills. The signature
   **horizon strip** (2px, samples the Aurora palette, driven by the existing 4 floats) is the only
   color in the instrument and the calm tell for an RT-yield.

## Consequences

- **The substrate becomes legible** — the lease/coordinator's behavior is finally observable, which
  is the missing success signal for ADR-0004/0010 work already shipped.
- **A new producer + a greenfield Plasma-6 plasmoid + a Rust→QML file-poll seam** are the real build
  cost; the producer is a near-clone of `feed.rs`/`monitor`. The plasmoid was the dominant unknown
  and is now **de-risked** (`spikes/keyhole/`): the three representations load, the package installs
  via `kpackagetool6`, native popup blur is the hosted-Dialog default (no shader), and the
  `DataSource` file-poll updates live — proven by a passing contract test. Both registers now render correctly
  when hosted by a real `plasmoidviewer` on the live plasmashell 6.6.5 / Wayland session, and the
  applet installs and runs **live on the desktop panel** (see Status/next item 5); only the
  SystemTray-containment click contract remains a documented v1 limitation.
- **The category we position to own is "the arbitration window for local AI"** — see
  `keyhole-positioning.md`. Risk: read as "a fancy nvtop" if arbitration isn't the primary visual.
- **No new write-path or non-determinism enters state-mutating code** in v1; every steer action
  waits on its enabling ADR.

## Status / next

Proposed. (1) ✅ Feasibility spike DONE and VIABLE (`spikes/keyhole/`): three representations,
`kpackagetool6` install, native blur, and a live `Plasma5Support.DataSource` file-poll proven by a
passing contract test; the read mechanism is `DataSource`, not XHR (see Decision §3). Before
`Accepted`: (2) fold the "design deltas" (measured snag-contrast, `acting`-row look, UNKNOWN
precedence, lead-with-arbitration) into this ADR; (3) the human confirms the Hermes approval-WRITE
path (gates P2 approve); (4) ✅ DONE — the `agentosd keyhole` producer mode is built (`keyhole.rs`,
read-only sibling of `feed`, own NVML handle, honest UNKNOWN, adaptive cadence, schema-1
`keyhole.json` pinned by a round-trip test, installed as a `--user` service alongside `feed`);
✅ the `lease` daemon's off-lock push to a `lease.json` mirror is also built and **verified live**
(snapshot under the lock, atomic temp+rename off it; Spawn→batch holder, then interactive preempt
narrated as `interactive preempted \`sleep\` (fits)` and merged into `keyhole.json`); the arbitration
headline is real. (5) ✅ DONE — verified in a real Plasma 6 host. `plasma-sdk`/`plasmoidviewer` was
installed on the live plasmashell 6.6.5 / Wayland session and hosted the installed
`org.agentos.keyhole` package: the full representation renders correctly (arbitration-led layout,
the cool VRAM aurora horizon gauge, the glyph aurora-ring, em-dash UNKNOWN honesty), and **both the
dark and light registers** were confirmed — the light-register aurora gauge/ring was the open item
from the frontend-design pass, cross-checked against deterministic offscreen `grabToImage` renders
of the shipped components. The applet was then declared tray-eligible
(`X-Plasma-NotificationAreaCategory=SystemServices` in `metadata.json`) and installed **live on the
desktop**, where plasmashell auto-instantiated it on the panel and measured its popup — loaded clean,
no QML errors, the `working` ◐ glyph + aurora ring visible in situ. Remaining caveat — the
**SystemTray-containment click quirk** (`CompactRepresentation.qml`) is still untested in-tray (the
live placement is a regular-panel widget, where click-to-expand works and the quirk does not apply);
v1 accepts it as a known, documented limitation, tracked for the P2 follow-up — not a blocker. With
(5) done, the open pre-`Accepted` gate is the human confirmation of the Hermes approval-WRITE path
(item 3, gates P2).

## Amendment (2026-06-16): the glyph reflects real GPU work, not just Hermes tasks

The original `state` grammar derived `idle/working/needs_you/snag` purely from the Hermes kanban
fleet counts. In practice that left the glyph reading **idle while the GPU was at 100%** under a
non-Hermes workload (ComfyUI generating, a direct model) — a "reassuring null" of exactly the kind
§4 warns against. Per user direction, `derive_legible` now promotes a reachable-but-idle fleet to
`working` when the GPU is **genuinely busy with a real workload**, gated on `util ≥ GPU_UTIL_MIN`
(25%) **AND** (`VRAM_used ≥ GPU_VRAM_MIN_FRAC` (20%) of total **OR** a resident Ollama model). The
AND is deliberate: the **reactive shader wallpaper (nimbus aurora)** burns GPU compute but holds
~no VRAM and loads no model, so it can never trip the glyph. The override never masks `unknown` (an
unreachable Hermes stays honestly unknown) and never downgrades a higher live signal
(`needs_you`/`snag`/a Hermes `working`); `floats.busy` carries the util level so the horizon strip
ramps with it. No schema change (still schema 1). Covered by the unit test
`gpu_work_promotes_idle_to_working_but_shader_stays_idle`.

## Amendment (2026-06-16): the aurora reaches the glyph and the VRAM gauge (frontend-design)

§7 set the 2px horizon strip as "the only colour in the instrument." Per user direction
(`/frontend-design`), the **nimbus-aurora presentation now also dresses the state glyph and the
GPU/VRAM readout** — the two surfaces a user actually reads *"is it working, and is the GPU under
pressure?"* from. The glyph gains a thin aurora **ring + soft halo** (`AuroraRing.qml`) sampled
from the SAME `horizonColor` the strip uses; the VRAM bar becomes a cool aurora **horizon gauge**
(indigo → blue → violet) that fills with usage and brightens with `busy`.

This **evolves, not breaks, the non-negotiables**: the colour stays *earned* (invisible/flat at
rest, blooming with load — "density-grows-with-load"); the SHAPE glyph + text label remain the
contrast-locked, never-colour-only carriers of state (the halo rides behind, AA untouched); the
reserved **warm** stays exclusive to `needs_you` (the cool ramp drives GPU pressure; the warm is
never mixed into the gauge); **never red**; and it is still **zero-GPU** — pure QtQuick gradients +
tweens, no shader, no Canvas (the §7 VRAM-coexistence reason holds). The horizon strip remains the
spine; the glyph-ring and gauge are the same aurora, sampled. Tokens canonicalised in
`integrations/design/instrument-tokens.md` ("Aurora ramp (cool)").

## Amendment (2026-06-16): condensed SYSTEM row earned motion — the contained ember (design-council)

The condensed SYSTEM section row (the progressive-disclosure fold: an all-one-tone section
collapses to a single tinted header, `FullRepresentation.qml`/`ServicesModel.qml`) was the one
**dead** surface on an otherwise living instrument. The design-council brief
(`docs/design/keyhole-condensed-row-earned-motion.md`, 7.72/10 → build) decided: **motion is the
off-nominal signal.** Within the locked **symmetric-tint** colour model (green sections *are*
painted green — a known, bounded calm-at-rest cost recorded here, recovered only partially via the
motion-asymmetry below, not via colour absence):

- **Healthy/transitional rows arrive then rest still.** A real tone change dawns the header tint in
  over 2500ms OutCubic (the HorizonStrip sunrise curve); a steady poll moves nothing. The
  transition is **latched** in the model (`_prevTones`) so the 8s poll can never re-fire it.
- **The attention row is a contained warm ember** — a translucent wash derived from `skin.warm`
  (#FF9957) that **gathers from the low edge** (mirroring the wallpaper dawn), one-shot bloom-in
  (1400ms OutCubic) on the transition into attention, then holds a **steady glow — no sustained
  breath** (product-owner call, resolving the council's split). Being motionless at rest, it is
  **categorically distinct from** the wallpaper's reserved `needs_you` dawn-*breath*: a SYSTEM
  `attention` (failed/down/unreachable daemon) is a *different predicate* than the feed's
  `needs_you`, so the reserved-warm rule ("`needs_you` exclusive") is honoured by construction —
  this is a *visual-only redundant cue* and triggers **no earcon, no toast**.
- **Collapse/expand is an opacity fade at fixed `boardPx`** (members fade, caret rotates) — **no
  height tween** (a height tween courts the `contentHeight→0` popup-clip bug `boardPx` exists to
  prevent, and WCAG 2.3.3 flags sliding panels). The signature beat is the **recede**: a section
  healing to uniform-healthy folds shut with the sunrise — the instrument exhaling as the system
  settles.

This **evolves, not breaks, the non-negotiables**: still **zero-GPU** (QtQuick gradients/tweens,
no shader/Canvas — §7 VRAM reason holds); the SHAPE glyph + a count-bearing predicate
(`Accessible.name`, reusing `summaryString()`'s wording) remain the never-colour-only carriers; the
toggle is now **keyboard-operable** (Return/Space + a shape focus ring that survives the wash);
**reduced-motion collapses every tween to instant** and holds the ember at one legible still crest;
and **honest UNKNOWN** holds — a stale/unreachable board performs no motion at all. Two pre-existing
accessibility gaps the motion work surfaced (mouse-only toggle, colour-only health assertion) are
fixed in the same change. Constants canonicalised in `instrument-tokens.md` ("Section wash (ember)").
Gated on `ui-accessibility-reviewer` (both-register AA + reduced-motion) and
`design-technologist`/`rater-feasibility` (popup-open Δframe-time + checked-in at-rest hash). The
`ambient-embodiment` / `responsible-ai-privacy-skeptic` "no `needs_you` impersonation" gate is
discharged by the steady (breath-free) ember.

## Amendment (2026-06-16): accessibility + embodiment hardening (advisory-review pass)

A `ui-accessibility-reviewer` + `ambient-embodiment-reviewer` pass on the aurora work (after the
in-host verification) confirmed the headline worry is a non-issue — the dark-register glyph holds
**6.56:1 at busy=1.0** (the ring sits outside the glyph ink; the halo opacity caps at 0.32), so the
bloom never washes out the glyph. It also surfaced a short fix-list, now applied:

- **Light-register `needs_you` was 1.90:1** — the bright `#FF9957` glow hue was being used as
  *foreground* on the near-white base. Split the reserved warm: `warm` stays the bright **glow/halo
  fill** (identical across registers); a new **`warmText`** token (`#FF9957` dark / **`#A8480F`**
  light, ~5:1) carries the **foreground** glyph + label. The "one warm element" rule is unchanged —
  only its light *literal* is now AA-safe (same dual-register pattern as `blue`).
- **Screen-reader semantics built** (§7 promised "`aria-live` on transitions"): `Accessible.role` +
  `Accessible.name` on the state token (keyed off `effectiveState`, so spoken == seen) and the
  VRAM/lease readouts. True transition announcement still rides the swaync `needs_you` toast (§5).
- **Light VRAM gauge dropped <3:1 at high busy** (lightening a light fill on a light track). The
  busy-lift now applies in the **dark register only** (`bodyLift`); light holds the base ramp and
  lets the leading-edge crest carry the "brightens with load" feel. Light `dim` nudged
  `#6B7184`→`#5A6173` (≥4.5:1).
- **`needs_you` no longer blooms to full at zero GPU load** — `intensity` was pinned `1.0` (warmth
  and bloom-size conflated). Now a **calm fixed floor (0.5)**: warmth asks for you, *size* is still
  earned by load. The glyph **bold** weight moved to a dedicated `emphasized` channel (decoupled
  from intensity).
- **UNKNOWN is now coherent across all three surfaces** — the strip's "dim de-energized memory" is
  joined by a **faint cold ghost ring** (intensity 0.10) on the glyph, so the foveal target also
  reads de-energized, distinct from idle's true (byte-quiet) nothing.
- **`working` intensity floor lowered `0.4`→`0.15`** so the bloom grows from near-dark; the
  per-surface `ringIntensity`/`breathing` derivations were **centralised into `KeyholeModel`**
  (`ringIntensityFor`/`breathingFor`) so the tray glyph and panel token sample ONE source (no
  drift); and the glyph text now holds **full opacity** when live (the ring/halo carry the breath,
  never the legibility-bearing shape).

No schema change; `qmllint` clean, contract test green, and the fixes were confirmed by offscreen
renders in both registers. The new `warmText` token is canonicalised in `instrument-tokens.md`.

## Amendment (2026-06-17): the glyph porthole earns the REAL animated aurora (one scoped shader)

§7 set the instrument as zero-GPU ("no custom shader"), and the three amendments above
faked the glyph's aurora with QtQuick `GradientStop` tweens — a breath + a drift. Per user
direction, the glyph porthole now shows the **real animated nimbus-aurora flow**: a small
`ShaderEffect` (`AuroraRing.qml`) running `porthole.frag.qsb`, the SAME "Flow" domain-warp the
wallpaper (`com.nimbus.aurora`'s `aurora.frag`) and the status-panel backdrop
(`spikes/ambient-backdrop/aurora-web.html`) use, re-paletted to the deep-navy instrument
register and fed the SAME live `busy/warm/snag` floats — so the keyhole and the wallpaper move
together, and the QML glyph and the HTML panel now run one identical aurora.

**This supersedes §7's LITERAL "no shader" for this ONE surface, while honouring its DEEP
intent — protect VRAM for inference.** The guarded resource is VRAM, not compute (the prior
amendment already records that the shader wallpaper "burns GPU compute but holds ~no VRAM"): a
<100px porthole holds a few KB of texture, and the same shader family already runs full-screen
as the wallpaper, so the marginal cost is rounding error. The exception is **bounded**:

- **Scope:** ONLY the foveal glyph porthole earns the shader. The signature **horizon strip**
  and the **condensed SYSTEM row** stay zero-GPU (QtQuick gradients/tweens) exactly as §7/the
  condensed-row amendment require — this amendment does not touch them.
- **Frame-capped:** the `iTime` clock advances on a **~30fps `Timer`**, below the display
  refresh — not an unbounded render loop.
- **Pauses when off screen; reduced-motion freezes.** When hidden or under reduced-motion the
  clock HOLDS at a representative frame — the disc shows a single **still** aurora (never black),
  like a reduced-motion wallpaper. The tray placement still **vanishes at true idle**
  (`PassiveStatus`), so at rest in the tray nothing renders at all. (Production follow-up: the
  always-present panel/desktop placement should also pause the clock on popup-collapse via the
  host's visibility signal; the spike gates on `visible` + reduced-motion, the hook for it.)
- **Evolves idle:** the porthole now **drifts gently at idle** (like the wallpaper, which is
  never frozen), with `busy` speeding the flow so working/idle differ by PACE as well as
  brightness — a more faithful "porthole onto the living sky" than the frozen gradient. This is
  the deliberate, user-directed departure from "invisible/static at rest"; honest UNKNOWN still
  holds (a cold, colourless, de-energized ghost) and reduced-motion still stills it.

**a11y is unchanged.** The SHAPE glyph stays a light ink + dark outline over a soft scrim, so it
remains AA over the brightest crest / warm bloom; `state` stays carried by shape + text + mood,
never colour-only; `snag` is desaturate+dim, **never red**; the reserved warm is the porthole's
low dawn-glow on `needs_you` only. The circular porthole is masked **in the shader** (an
anti-aliased alpha circle → transparent corners), so AuroraRing needs **no mask item and no new
QML import** — it stays QtQuick-only, like the rest of the keyhole.

**Verified on the live plasmashell 6.6.5 / Qt 6.11 Wayland session** (offscreen has no GL
context → blank frames; this was the trap that sank earlier tries — capture MUST be on-session):
`porthole.frag` compiled with `qsb --qt6` (the wallpaper qsb's exact target set: SPIR-V/GLSL
100es·120·150/HLSL 50/MSL 12); a `grabToImage` of all five moods plus a t=2/10/20 motion check
(`spikes/keyhole/portholetest.qml` → `portholetest.png`) confirms the circular mask, the deep-navy
flow, faithful per-mood reactivity (warm-low, snag-grey, unknown-ghost), and live advection; the
integrated component (`ringtest.qml` → `ringtest.png`) confirms the glyph stays legible over the
flow with rim + earned halo intact. `qmllint` clean on `AuroraRing.qml` and its consumers. No
schema change. The shader is GPL-3.0-or-later (derived from nimbus aurora); the QML stays MIT.
