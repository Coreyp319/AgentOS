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
