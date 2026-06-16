# Design brief — the keyhole (the AgentOS legibility instrument)

- Status: Design brief, council-approved direction (aggregate 8.1/10, uncapped). Feeds ADR-0012.
- Date: 2026-06-16
- Council: 10 design voices → mediate → 5-dimension rate → aggregate → market triangulation → delight.
- Companions: `keyhole-scorecard.md` (ratings), `keyhole-positioning.md` (market).

## Problem

AgentOS can answer *"is the agent busy?"* (the reactive wallpaper) but cannot honestly answer
*"what is it doing right now, and is it OK?"* — and the substrate's headline work (the VRAM
coordinator / lease) is **invisible**: it only `println!`s. Today the sole structured output is
`agent.json` = four scalars (`state/busy/warm/snag`) driving the wallpaper *mood*; `monitor`'s
rich VRAM/model data is ephemeral stdout; `lease` exposes tier/holder only over `busctl`;
tokens/sec is measured nowhere. There is no legible, taskbar-reachable surface and no management
surface. The keyhole closes that gap **without reinventing Hermes**.

## The decisive reframing (don't-reinvent)

Hermes already ships a full React/Vite kanban dashboard on `http://127.0.0.1:9119`
(`hermes dashboard`). Building a board in agentosd rebuilds the Hermes UI — the ADR-0001
anti-pattern. So the original *"(1) board, else (2) throughput"* ordering is **inverted**:

- **The board is a LINK-OUT** (`xdg-open http://127.0.0.1:9119`), gated on gateway-alive.
- **The keyhole's body IS the resource/throughput instrument** — VRAM pressure, model residency,
  and the **GPU lease/arbitration state** (who holds the GPU, at what tier, and the preempt that
  resolved the fight). This is the only datum no competitor can fake — it is backed by shipped
  code (`org.agentos.Coordinator1`, ADR-0010). **Foreground arbitration; demote commodity gauges.**

## Direction

A **calm instrument panel** in glass-cockpit grammar: a native KDE blurred system-tray popup
(no custom shader — it must never compete with inference for VRAM), **quiet-dark at rest, density
grows with load**. The keyhole is the pull-only, closed-by-default **foveal zoom** from the
ambient wallpaper mood: glance at a warm wall → warm tray glyph → click → the thing that needs you
is already on top. It may surface *itself* only on a `needs_you` transition, via a swaync toast —
never auto-popping. At true idle the tray glyph **vanishes** (byte-identical-to-baseline).

### Three sizes

```
[tray glyph]   state-tinted + glyph SHAPE (never color-only); vanishes at idle
     │ hover/focus
     ▼
┌─ PEEK (~280px) ───────────────┐   ← 2px horizon strip (samples Aurora palette)
│ ◐ working · 3 active          │     glyph + TEXT label
│ 1.2k tok/s   ▁▂▃▅▃            │     big numeral · plain label · microtrend
│ 6.1 / 8 GB · lease: interactive│
│ ▸ needs you: "approve deploy?"│   ← the ONE warm element, only when warm>0
│ Open board ↗                  │     link-out → :9119
└───────────────────────────────┘
     │ click/Enter
     ▼
┌─ FULL instrument ─────────────────────────────────┐ ← horizon strip
│ ◐ working · 3 tasks              [needs_you ●]     │ ← one warm element
├─────────────── ARBITRATION (the lead) ────────────┤
│ LEASE   interactive (Hermes)   ·  batch: queued    │
│ PREEMPT wallpaper yielded ~1.5GB → qwen2.5 loaded  │  ← the signature truth
├───────────────┬───────────────┬───────────────────┤
│ THROUGHPUT    │ RESIDENCY     │ VRAM               │
│  1.2k tok/s   │ qwen2.5:14b   │ 6.1 / 8 GB ▔▔▔▔▁   │
│  ▁▂▃▅▃ 60s    │ loaded 4m     │                    │
├───────────────┴───────────────┴───────────────────┤
│ Open board ↗                                       │
└────────────────────────────────────────────────────┘
```

## States (the honesty contract)

`state` (not the floats) is the **single source of truth** for every affordance.

| state      | user-facing       | treatment                                            |
|------------|-------------------|------------------------------------------------------|
| idle       | *(no chip)*       | glyph + strip only; "Nothing running right now."     |
| working    | **Working**       | luma-lift, calm                                      |
| needs_you  | **Needs your OK** | the ONE warm element (reserved dawn-glow)            |
| acting     | **Acting** *(ship the look now; data deferred)* | chevron glyph + dot |
| snag       | **Paused — waiting** | cool/dim, desaturate, **never red**               |
| **UNKNOWN**| **Status unavailable — can't reach Hermes** | dimmed memory of last strip behind text; em-dash readouts |

**The #1 fix:** `feed.rs` does `read_fleet(...).unwrap_or_default()`, so a dead gateway /
unreadable kanban / stuck WAL **all render as calm-idle** — the desktop looks calmest when most
broken. The keyhole carries a **first-class UNKNOWN**, distinct from idle and from a real `0`,
driven by `gateway_state` + `keyhole.json` mtime freshness. UNKNOWN is *polite* (not assertive)
while Hermes is merely restarting (`degraded`); assertive only when sustained.

## Data architecture (the safety boundary)

- **A new read-only `keyhole.json` producer** (sibling agentosd mode) — NOT a widening of the
  4-scalar `agent.json` wallpaper contract. Atomic temp+rename (clone `feed.rs::write_feed`),
  edge-driven, **adaptive cadence**: 2s idle → back off 5–10s under load / on slow NVML →
  skip-on-stale (never overlapping NVML queries).
- **The UI consumes JSON files only.** It must **never** open NVML or call the lease D-Bus
  per-render: `Status` locks the same `Mutex<Inner>` that gates `Acquire`/`Spawn`/preempt
  (`lease.rs:342`) — **a render must never be able to delay a SIGKILL.**
- **Read mechanism (proven by the spike): a `Timer`-driven `Plasma5Support.DataSource`** (re-issued
  `cat` per tick), **not `XMLHttpRequest`.** Under plasmashell 6.6.5 / Qt 6.11, XHR on `file://` is
  fully disabled (returns empty; plasmashell sets no `QML_XHR_ALLOW_FILE_READ`) — worse than the
  "goes stale" risk the brief originally assumed. `DataSource` is async + non-overlapping, giving
  skip-on-stale for free. Cadence comfortable at 2s idle / 5–10s loaded.
- **The lease daemon PUSHES its state** to a sibling `lease.json` mirror on transition (which the
  `keyhole` producer merges), **fire-and-forget off the lock** — snapshot taken under `Inner`,
  atomic temp+rename written after it is dropped; never `fs::write` while holding the lock. ✅ Built
  + verified live (Spawn→batch holder, interactive preempt narrated + merged into `keyhole.json`).
- **Pin the contract:** a serde round-trip test on an exact string + a versioned `schema` field
  (today `feed.rs:344` is the only thing keeping producer/consumer in sync).

## Motion (calm, zero-GPU)

QML property animation only (no Canvas/sparkline repaint cost). Number-tweening 900ms `OutCubic`
(interpolate the underlying value, round the displayed — no last-digit flicker; **snap to em-dash
instantly** when a stream stops — honesty is faster than flattery). A 3.2s breathing liveness dot
deliberately **not** synced to the 2s poll (reads as alive, not ticking). Staggered 80ms task
transitions (no popcorn). `prefers-reduced-motion` clamps **the tweens too**, not just the dot;
every motion has a still equivalent.

## Accessibility canon

Redundant encoding **shape/icon + TEXT + color**, never color-only (idle ○ / working ◐ /
needs_you ● / acting ▸ / snag ▢-dashed). A **contrast-locked status token** outside the
personalization envelope, AA-validated in the worst case (`working` luma-lift under `snag`
desaturate). `aria-live` only on **transitions** (assertive: needs_you/snag; polite: task-done) —
never on the 2s re-render. Focus preserved across live updates (stable keys; needs_you announces
but does not steal focus). Full keyboard path: tray → panel → task → action, Esc drills out.

## Signature delight (within the non-negotiables)

1. **The yield, narrated as a sunrise** (the screenshot). When the coordinator evicts the RT
   wallpaper (~800ms flicker, ADR-0004) so a model can load, the horizon strip — the one thing
   that survives the relaunch — does a single slow brightening sweep, and the peek tweens in:
   *"Wallpaper paused — the agent needed the GPU to think."* The eviction **is** the product; no
   other desktop has a beauty it consciously, legibly sacrifices and says so. Zero GPU (2px
   gradient + opacity tween); reduced-motion holds the brightened still + the sentence.
2. **The zoom as one continuous lean-in.** Glyph and peek read the *same* `warm` float at
   click-time; the popover's first frame inherits the glyph's exact dawn-glow, then settles —
   one bloom sampled twice, not a second warm source. The desktop, leaned into.
3. **The warm bloom drains, doesn't switch off.** On needs_you→resolved, warmth recedes like a
   tide over ~2.5s on the same low-pass the wallpaper uses — you watch your own resolution settle.

## v1 cut line (what ships)

- Tray glyph (idle-vanish) → PEEK → FULL instrument; native KDE blurred popup.
- Read-only readouts: VRAM pressure, model residency (`/api/ps`), **lease tier/holder + preempt
  event (the lead)**.
- First-class **UNKNOWN** state.
- tokens/sec rendered **UNKNOWN** until measurable.
- Board **link-out** to `:9119` (gateway-gated).
- Horizon strip driven by the existing 4 `agent.json` floats; never-red snag.
- A11y canon + calm motion as above.

## Deferred (with dependency — never promised in v1)

- **approve `needs_you` / pause / cancel → P2** — needs a *confirmed* Hermes approval-WRITE API
  (today only the read signal `needs_you.json` exists). v1 adds NO write-path of its own.
- **tokens/sec (live) → P2** — needs the ADR-0002 transparent axum proxy summing
  `eval_count`/`eval_duration` from the Ollama stream. Never synthesized.
- **revert "what the agent changed" → P3** — needs ADR-0005's apply/rollback tx + ledger
  (do not exist in `src/`). No honest undo until then.
- **RT-yield strip tell** — needs an `rt_yielded` signal from the coordinator; render UNKNOWN
  until emitted.

## Gap plan to 9/10 (from the aggregator)

- **A — design, fold in now:** measured snag-contrast per state; ship the `acting` row look;
  UNKNOWN precedence + polite-when-degraded; horizon strip bound to the never-red snag law;
  reduced-motion clamps tweens; named frame/power budget; **lead-with-arbitration**.
- **B — spike/build:** ✅ Plasma-6 plasmoid spike DONE and VIABLE (`spikes/keyhole/`) — 3
  representations load, `kpackagetool6` installs, native blur is the hosted-Dialog default, and the
  `DataSource` file-poll updates live (contract test passes). ✅ The **producer is built** —
  `agentosd keyhole` (`crates/agentosd/src/keyhole.rs`): read-only sibling of `feed`, own NVML
  handle, honest UNKNOWN, adaptive cadence, schema-1 `keyhole.json` pinned by an exact-string
  round-trip test, shipped as a `--user` service via `dist/{apply,restore}.sh`. Verified live
  (`--once` emits a valid contract; 46 tests green). ✅ The `lease` daemon's off-lock push to a
  `lease.json` mirror is also built + verified live (the arbitration headline — tier/holder/preempt
  — is real). Remaining: a QML density harness; the in-tray screenshot once `plasmoidviewer` is
  installed.
- **C — downstream-blocked:** tokens/sec→proxy, approve→Hermes write-API, revert→ADR-0005 tx.

## Open question for the human

Confirm the **Hermes approval-WRITE path** before P2 design. (a) Hermes exposes a documented
approve endpoint → P2 approve is a pure gated call, no AgentOS tx (recommended); (b) none → approve
stays a link-out into `:9119` and AgentOS never grows a write-path. Until resolved, v1 ships
strictly read-only.
