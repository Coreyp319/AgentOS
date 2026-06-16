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
  `DataSource` file-poll updates live — proven by a passing contract test. Only the final in-tray
  drag is doc-confirmed (no `plasmoidviewer` in this environment).
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
headline is real. Before `Accepted`: (5) screenshot the in-panel tray once `plasmoidviewer` is
available (the one doc-confirmed-only step).
