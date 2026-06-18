# Design 0023 — window-drag → wind producer (KWin desktop-signal binding)

Resolves **open questions 5 and 6** of
[`0023-creative-environment-pipeline.md`](0023-creative-environment-pipeline.md) — the
"Interactivity — live desktop-signal bindings" section. That spec named the canonical binding
("the wind direction follows the direction a window is dragged; drag speed → gust strength") and
deferred the *mechanism*. This is the mechanism: a real, reversible **KWin window-drag producer**
that lands inside the substrate's existing producer→consumer grammar (ADR-0001) instead of
inventing a new one.

**Status (2026-06-18): Proposed — producer prototyped, sink BUILT + unit-tested.** The producer
half (the KWin script) is built and lints clean (`spikes/window-drag-wind/`). The **sink half is now
BUILT** (ADR-0023 P1): `org.agentos.Wind1` is served by the `lease` daemon (home A, §3.1) and the
deterministic spring + atomic `wind.json` writer run in a separate tokio tick task —
`crates/agentosd/src/wind.rs`, wired from `lease::serve` via `wind::attach`. It is **unit-tested**
(idle byte-stability, clamp/renormalize/NaN, ease-back-to-exactly-0, missed-`Finished` calm, and a
no-lease-lock structural assert) and `cargo build`/`cargo test -p agentosd --bins` are green. **The
live KWin→D-Bus round-trip is still UNVERIFIED** (no interactive-drag harness in this pass — see
"Honest unknowns" §1). The shader uniform read (consumer) remains a downstream spike. Not yet an
ADR; this feeds the ratification of ADR-0023.

## Where the prototype lives

`spikes/window-drag-wind/` (throwaway sandbox, excluded from the cargo workspace per CLAUDE.md):

- `kwin-script/` — the loadable KWin/Script KPackage (`metadata.json` + `contents/code/main.js`).
- `wind.schema.json` — the `wind.json` runtime contract (JSON Schema, version 1).
- `wind_sink_sketch.rs` — the reference Rust the **shipped sink was ported from** (the deterministic
  spring + atomic write). The real, wired implementation is now `crates/agentosd/src/wind.rs` (home A,
  §3.1); the sketch stays as the throwaway origin.
- `apply.sh` / `restore.sh` — reversible install/uninstall.
- `README.md` — run instructions + the verified-vs-unverified split.

## The shape (matches the substrate exactly — ADR-0001)

```
window MOVE                KWin script              session D-Bus            agentosd "wind" sink            shader consumer
(user drag) ─ deltas ─▶  frameGeometry diff ─▶  Gust(dirX,dirY,speed,active) ─▶  spring + atomic write ─▶  wind.json ─▶ windDir/gust uniforms
                         (sandbox: read only)     (callDBus, async)            ($XDG_RUNTIME_DIR/nimbus-aurora/)   (Timer poller, eased)
```

This is the **same** producer→consumer pattern `feed.rs` already runs for `agent.json`
(`feed.rs:235-241` atomic write; `feed.rs:88-112` derive). `wind.json` is a **new sibling file**,
not a widening of `agent.json` — the same discipline `keyhole.json` followed (ADR-0012 §2).

---

## 1. Source signal — which KWin API surfaces a live window-drag

**Plasma 6 / KWin 6, Wayland.** The old `Client`-era `workspace.clientStartUserMovedResized` /
`clientStepUserMovedResized` / `clientFinishUserMovedResized` signals were **renamed** in the 5→6
port: `Client` → `Window`, and the move/resize lifecycle moved onto **per-window** signals on
`KWin::Window`:

| Signal (KWin 6) | Fires |
|---|---|
| `Window.interactiveMoveResizeStarted()` | the user begins dragging/resizing the window |
| `Window.interactiveMoveResizeStepped(QRectF geometry)` | **per drag step** during the operation — the live feed |
| `Window.interactiveMoveResizeFinished()` | the operation ends |

We attach handlers to every window via `workspace.windowAdded(KWin::Window)` (plus the windows
already in `workspace.stackingOrder` at load time). Window identity for *attaching* is never read
beyond the disambiguation flags in §4.

**Deriving the vector.** On `Started` we snapshot the **centre** of `Window.frameGeometry`
(a `QRectF` with `x`, `y`, `width`, `height`). On each `Stepped` we read `frameGeometry` again and
compute:

- `Δ = centre_now − centre_prev` (pixels), `Δt = now − t_prev` (seconds, from `Date.now()`);
- **direction** = `Δ / |Δ|` — a unit vector in **screen space** (Wayland: x right+, **y down+**);
- **speed** = `|Δ| / Δt` px/s, normalized to `[0,1]` against `SpeedAtFull` (default 1600 px/s).

A **deadband** (`MinDeltaPx`, default 2 px) drops sub-pixel jitter; a **throttle**
(`MinIntervalMs`, default 50 ms ≈ 20 Hz) caps bus traffic. We re-read `frameGeometry` rather than
trusting the `Stepped(QRectF)` argument so a single property is the source of truth (the argument
and the property agree, but re-reading is robust to any version skew).

We react to a **MOVE** by default, not a resize (`Window.move` vs `Window.resize` distinguishes
them) — "wind follows where you *shove* the window" reads most honestly from a translation.
`ReactToResize` is a knob, default off.

**Cite:** KWin Scripting API, `develop.kde.org/docs/plasma/kwin/api/` (Workspace `windowAdded`,
`stackingOrder`, `cursorPos`, `virtualScreenSize`; Window `interactiveMoveResize*`,
`frameGeometry`, `move`, `resize`, `normalWindow`); KWin scripting tutorial,
`develop.kde.org/docs/plasma/kwin/`.

## 2. Output contract — `wind.json`

Written beside `agent.json` in `$XDG_RUNTIME_DIR/nimbus-aurora/` (the `/run/user/<uid>` fallback
that `feed::feed_dir` uses). Versioned (`schema: 1`) per the ADR-0009 two-consumer lesson. Full
JSON Schema: `spikes/window-drag-wind/wind.schema.json`.

```jsonc
{
  "schema": 1,
  "dir":    [-0.92, 0.39],   // eased UNIT vector, screen space (x right+, y down+)
  "gust":   0.61,            // eased [0..1] gust strength
  "active": true,            // true while a drag is live; false on end / stale
  "updated_at": 1750000123.4 // epoch secs of the sink's last write
}
```

**Neutral / idle frame** (byte-stable, the diff anchor — see §5):

```json
{"schema":1,"dir":[0,-1],"gust":0,"active":false,"updated_at":<t>}
```

Conventions matched from `feed.rs`: atomic write via dot-prefixed temp + rename
(`.wind.<pid>.tmp` → `wind.json`, so a `*.json` poller never reads a half-written file,
`feed.rs:235-241`); `round3`-style rounding so the idle string is stable and diffable;
edge-write (only write when the rounded string changes) so an idle desktop stops touching the
file and the consumer simply holds last-good.

## 3. Calm + determinism — where the low-pass lives

**The low-pass lives in the producer side (the sink), and the consumer (shader) adds its own
ambient damping** — *not* in the KWin script. Three reasons:

1. **The sandbox can't do it well.** A plain-JS KWin script has no reliable timer primitive
   (no `setTimeout`/QML `Timer` in the `javascript` API), so it cannot run a fixed-tick spring
   that keeps easing *between* drag steps or decays gust *after* a drag with no further events.
   It can only emit on a signal. So the script emits **raw** deltas and the sink owns the spring.
2. **Determinism belongs in code (ADR doctrine: model proposes, code disposes).** The mapping
   raw-vector → eased uniform is a **pure deterministic function** — a critically-damped
   first-order spring, `x += (target − x)·(1 − e^{−ω·dt})`, with `ω ≈ 1.5` rad/s — the same eased
   family the reactive-wallpaper QML *consumer* applies to `busy`/`warm`/`snag` (NB: `feed.rs:88-112`
   is a stateless count→intensity `ramp` emitting edge-driven scalars — the temporal smoothing has
   always lived in the consumer, never the producer). No model in the live loop.
3. **Calm is non-negotiable.** Wind that snaps to every drag is an attention magnet — the precise
   thing the ambient vision forbids. The spring makes a drag *nudge* the wind and ease back over
   ~1.5–2 s, below the attention-capture threshold. `gust` is bounded `[0,1]`; a teleport-sized
   delta is clamped at both the producer (defensive) and the sink (authoritative).

After a drag, the sink **holds the last-good direction and decays gust → 0**, then relaxes
direction back to neutral `[0,-1]` as gust crosses ~0 — so a long-idle desktop sits at the
canonical resting wind. The shader consumer **polls** `wind.json` on a `Timer` (never sync-XHR on
a relative path — that silently leaves uniforms at 0, `spikes/hills-reactive/README.md`) and may
add a second, gentle ambient damping on top (omega ~1–2) exactly like the existing
`uMusicReact`/`uActiveMove` feeds.

**Consumer mapping (the neutral-vector contract, now explicit).** The consumer computes the wind it
applies as **`windDir = dir · gust`** — the eased unit `dir` scaled by the eased `gust`. The single
load-bearing consequence: **rest (`gust → 0`) ⇒ `windDir = (0,0)`** regardless of which way `dir`
last pointed, so a settled desktop has *no* wind, not a frozen breeze in the last drag's heading. The
sink upholds its half of this contract by easing `gust` to **exactly 0** at rest (a hard snap once it
crosses the rest band, `wind.rs::REST_GUST`) and keeping the idle `wind.json` frame **byte-stable**
(edge-write — it stops touching the file once neutral). `dir` is also relaxed back to the canonical
`[0,-1]` at rest so the idle string is the pinned anchor, but `gust = 0` is what makes the product
zero. The shader may add a second gentle ambient damping on top (omega ~1–2), as for the existing
`uMusicReact`/`uActiveMove` feeds.

Reference (the origin sketch + pinned idle-frame test): `wind_sink_sketch.rs`. **Shipped
implementation:** `crates/agentosd/src/wind.rs` (ported, then extended with the edge-write,
`org.agentos.Wind1` server, and the exact-0-at-rest snap the consumer mapping requires).

### 3.1 Structural cost — flag for the panel (the crate is synchronous and tiny on purpose)

The sink needs to **receive a D-Bus method call** (`Gust(...)`), which means a session-bus
**server**. The `feed`/`keyhole` producers are pure synchronous file writers with no D-Bus and no
async runtime; only the `lease` daemon serves D-Bus (on tokio/zbus, `org.agentos.Coordinator1`).
So this is **not a free increment** — it adds a serving surface. Two homes:

- **(A, CHOSEN + BUILT) Fold `org.agentos.Wind1` into the existing `lease` daemon.** It is already a
  zbus server on the session bus, already owns `org.agentos.Coordinator1` (the well-known name the
  KWin script's `callDBus` already targets), and already runs an event loop. Net new surface: one
  object path (`/org/agentos/Wind`, interface `org.agentos.Wind1`, method
  `Gust(d dirX, d dirY, d speed, b active)` — the exact tuple the producer's `callDBus` sends) plus
  a ~60 Hz tick task. Near-zero structural cost, no second always-on service. **This is what shipped
  (ADR-0023 P1):** `crates/agentosd/src/wind.rs` defines `WindSink` (the `Gust` handler) + a
  `tick_loop` task, mounted on the lease daemon's connection by `wind::attach`, called from
  `lease::serve`. A mount failure is logged, never fatal — the coordinator keeps serving
  `org.agentos.Coordinator1` regardless (fail-open).
- **(B) A standalone `agentosd wind` subcommand.** Cleaner separation, but a *second* always-on
  D-Bus server and a second tokio runtime — a real structural shift for what is one method.

**Recommendation: (A) — taken.** This keeps the "one D-Bus server" shape the substrate has today.
The runtime/frame-budget concern (the tick must never delay a preemption SIGKILL) is satisfied
**structurally**, not by discipline: `wind.rs` owns its OWN `Arc<Mutex<WindState>>`, a *different*
mutex from `lease::Inner`'s arbitration lock, and the module has no import of, field carrying, or path
to `Inner`. The `Gust` handler and the tick task only ever take the tiny wind mutex (held across a few
field writes, never across an `.await` or the file write — the atomic write is snapshot-under-lock,
write-after-drop). So the worst a misbehaving tick can do is contend its own lock; the lease lock —
and therefore the SIGKILL path — is unreachable from the wind code. `wind.rs::wind_path_takes_no_inner_lock`
pins this (a `WindState` is a plain `Copy` value; the test could not be *written* to touch `Inner`).

## 4. Privacy posture (load-bearing) — geometry deltas only

This producer observes window *behaviour*, so the posture is deliberately skeptical. **The only
data that ever leaves the KWin script is three floats and a boolean: `Gust(dirX, dirY, speed,
active)`.** There is no parameter — anywhere in the D-Bus signature, the schema, or the sink — that
can carry an identity.

**Emitted (and nothing else):**

| Field | Source | Why it's safe |
|---|---|---|
| `dirX`, `dirY` | normalized `frameGeometry` centre delta | a direction; says *which way a window moved*, not which window |
| `speed` | `\|Δ\|/Δt` normalized to `[0,1]` | a rate; reveals nothing about content |
| `active` | drag in progress? | a boolean lifecycle flag |

**Read by the script but NEVER emitted** (used *only* to decide whether to react):
`Window.normalWindow`, `Window.dock`, `Window.desktopWindow`, `Window.move`, `Window.resize`.
These gate out panels, the desktop/wallpaper layer, and resizes — a yes/no decision; the values
are never serialized.

**Explicitly NOT touched, NOT stored, NOT transmitted:** window `caption`/title, `resourceClass`
/ `resourceName` (the app identity), `pid`, `windowId`/`internalId`, `desktops`/activity,
absolute screen position (only *deltas* leave; the absolute centre is a local, per-drag scratch
value discarded on `Finished`), and window **contents** (a KWin script can't read pixels anyway).

**Why this is safe, stated for the privacy skeptic:** the signal is a **gesture, not a log**. It
is *aggregate motion* — you cannot reconstruct "Corey dragged Firefox at 14:03" from a stream of
unit vectors with no identity, no timestamp-of-action persisted to disk (the sink writes only a
rolling `updated_at`, edge-debounced), and no per-window attribution. The output file is
**runtime-only** (`$XDG_RUNTIME_DIR`, tmpfs, gone on logout), never persisted to `$HOME`. And the
mapping is **lossy by construction** — normalizing to a unit vector throws away magnitude/position;
clamping `speed` to `[0,1]` throws away the actual velocity. This is the same posture the substrate
already holds: `feed.rs` reads Hermes state read-only and emits four scalars; this reads window
geometry and emits three. **Hand-off:** `wayland-computeruse-reviewer` to confirm the KWin script
surface exposes nothing I missed; a privacy reviewer to confirm the gesture-not-log framing holds.

## 5. Fail-open — neutral wind, reversible

Per ADR-0003, every failure mode resolves to **neutral wind** (`dir [0,-1]`, `gust 0`), and the
shader resolves to its resting motion — **idle stays byte-identical** (ADR-0009's contract):

- **No producer (script not loaded / disabled):** `wind.json` is never created → the consumer
  holds its neutral default. Nothing on the desktop is touched.
- **No sink (the `Gust` method has no server):** the KWin script's `callDBus` is fire-and-forget
  with no callback → a harmless async no-op. The producer can be installed *before* the sink with
  zero ill effect.
- **Drag ends:** the sink decays `gust → 0` and relaxes `dir` to neutral.
- **Missed `Finished`** (a window destroyed mid-drag never fires it): the sink's
  `ACTIVE_TIMEOUT_S` (0.30 s with no new step) flips `active=false` and eases gust down anyway
  (belt-and-suspenders — `wind.rs::missed_finished_still_calms`).
- **Stale `wind.json`** (sink crashed): the consumer treats a file older than `STALE_SECS` as
  neutral. Because the file is edge-written and the last written frame after any drag is already
  neutral, a present-but-old file *already reads calm*.

**Reversible by construction (ADR-0005):** the live signal is a **uniform** — nothing is persisted
to desktop state. `restore.sh` removes the `kwinrc` `[Plugins]` key, uninstalls the package, and
clears the runtime file; the desktop returns to exactly its prior state. The idle-frame string is
pinned in a test (`wind.rs::idle_is_exactly_neutral_and_stable`, with
`idle_frame_is_byte_stable_across_ticks` guarding the across-ticks stability the edge-write relies
on) so a future "improve the spring" change can't silently drift idle off neutral (the idle-drift
pitfall).

## 6. Install / lifecycle — reversible, in the repo's `--user` spirit

The KWin script is a KPackage, loaded/unloaded the standard Plasma 6 way (no logout needed):

```bash
# install (apply.sh):
kpackagetool6 --type KWin/Script -u spikes/window-drag-wind/kwin-script   # -i if fresh
kwriteconfig6 --file kwinrc --group Plugins --key agentos-window-drag-windEnabled true
qdbus org.kde.KWin /KWin reconfigure        # hot-reload KWin's scripting subsystem

# uninstall (restore.sh):
kwriteconfig6 --file kwinrc --group Plugins --key agentos-window-drag-windEnabled --delete
kpackagetool6 --type KWin/Script -r agentos-window-drag-wind
qdbus org.kde.KWin /KWin reconfigure
```

`EnabledByDefault: false` in `metadata.json` — installing the package never auto-arms it; the
explicit `kwriteconfig6` enable is the consent gate (local-first / consent). This mirrors the
repo's `dist/{apply,restore}.sh` reversibility (the sink half, if built into the `lease` daemon,
ships via the existing `crates/agentosd/dist/apply.sh` that already installs `agentos-lease.service`
— no new unit needed under home (A)). Precedent for the whole shape — a KWin script that
`callDBus`-es a custom session-bus service, installed via `kpackagetool6`: the real-world
`maxiberta/kwin-system76-scheduler-integration` project.

---

## Honest unknowns (for the design-council to probe)

1. **Live-KWin verification not done.** I did not load the script against the live compositor in
   this pass (no interactive-drag harness here, and modifying the live desktop is out of scope for
   a spec). The script lints clean and uses only documented API, but the following want a live run
   (`wayland-computeruse-reviewer` + a live session):
   - Does `interactiveMoveResizeStepped` actually fire **per-step on Wayland**, or only at the end
     under some configs? (KWin's own docs warn that for `frameGeometryChanged` "depending on resize
     mode the signal might be emitted at each resize step or only at the end" — the move case is the
     one to confirm.) If it only fires at the end, the producer degrades gracefully to a single
     end-of-drag gust — directionally correct, just not continuous. Worth knowing before tuning.
   - Does `callDBus` reach a **custom** well-known name (`org.agentos.Coordinator1`) without a D-Bus
     policy file, or does the session bus need a `.conf`? (The system76 precedent forwards
     session→system; we stay session-only, which should need no policy — but verify.)
2. **Per-window vs aggregate (open question 5).** This prototype is **aggregate** — every window's
   drag feeds one global wind, last-drag-wins. That is the right default for a single ambient wind
   field. If a future multi-monitor / per-scene model wants per-window or per-screen wind, the
   schema needs a window/screen key — which **reintroduces an identity field** and reopens §4.
   Recommendation: stay aggregate; revisit only if a concrete need appears.
3. **Multi-monitor speed normalization.** `SpeedAtFull` is a single px/s constant; on a 4K vs 1080p
   screen the *feel* differs. `workspace.virtualScreenSize` is available to normalize by screen
   units instead of raw pixels — a `motion-designer` call on whether the gust should feel
   resolution-independent.
4. **The 60 Hz tick inside the lease daemon (§3.1, home A) — ADDRESSED in the P1 build.** The wind
   task is a *separate* tokio task owning its own `Arc<Mutex<WindState>>` (a different mutex from the
   lease lock; `wind.rs` cannot name `Inner`), so it provably cannot delay a preemption SIGKILL
   (`wind.rs::wind_path_takes_no_inner_lock`). The idle edge-write is in place: once the frame body is
   neutral the tick stops writing the file (`idle_frame_is_byte_stable_across_ticks`). What a
   `rust-performance-reviewer` / `resource-safety-reviewer` pass would still add: confirm the ~60 Hz
   `interval` wakeup itself (the tick *task* still wakes even when not writing) is an acceptable idle
   cost, or whether the tick should park when neutral and re-arm on the next `Gust` (a small
   refinement, left as a follow-up).
5. **The mapping `dir → windDir` is the consumer's, and per-style.** Like the `busy/warm/snag`
   grammar, the *same* wind vector lands differently on each scene; the amber-field grass bows one
   way, a different scene might not have a ground-plane "down". `generative-artist` /
   `motion-designer` own the per-scene wind table; this producer only delivers a clean,
   screen-space, eased vector. (And per the parent spec: the binding is **inert in the baked film
   artifact**, live **only** in the real-time shader/UE mode.)

## Sources

- [KWin scripting API](https://develop.kde.org/docs/plasma/kwin/api/) — `Window.interactiveMoveResizeStarted/Stepped/Finished`, `Window.frameGeometry`, `workspace.windowAdded`, `stackingOrder`, `callDBus`, `readConfig`, `virtualScreenSize`, `cursorPos`.
- [KWin scripting tutorial](https://develop.kde.org/docs/plasma/kwin/) — package layout (`X-Plasma-API`, `X-Plasma-MainScript`), `kpackagetool6 --type=KWin/Script -i/-r`, `kwriteconfig6 --group Plugins`, `qdbus org.kde.KWin /KWin reconfigure`, the QML/JS sandbox (`readConfig`, no general file I/O).
- [maxiberta/kwin-system76-scheduler-integration](https://github.com/maxiberta/kwin-system76-scheduler-integration) — real-world precedent: a KWin script that `callDBus`-es a custom session-bus service, installed via `kpackagetool6`.
- In-repo: `crates/agentosd/src/wind.rs` (the shipped sink — `org.agentos.Wind1` + the spring/tick task, ADR-0023 P1), `crates/agentosd/src/lease.rs` (`wind::attach` mount point + the `eevee-render` lease profile added alongside, ADR-0023 P1), `crates/agentosd/src/feed.rs` (the producer/atomic-write/spring pattern this mirrors), `crates/agentosd/src/keyhole.rs` (the second-sibling-file precedent, ADR-0012 §2), `spikes/hills-reactive/README.md` (Timer-poll-not-XHR; render-on-session-not-offscreen), `spikes/kwin-mcp-FINDINGS.md` (the kwin-mcp lane — note it does session *automation*, it does not run a persistent move-observer script, so this producer is genuinely new, not a duplication).
```
