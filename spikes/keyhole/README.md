# spikes/keyhole — feasibility spike for the AgentOS keyhole instrument

> **Promoted.** The shipped plasmoid now lives in `integrations/keyhole/` (driver-managed,
> `id: keyhole`). This directory is kept as the **validation record + test harness** only
> (`harness.qml`, `mutate.sh`, `runtests.sh`, the headless poll/contract proofs). Edit the
> canonical package under `integrations/keyhole/package/`, not here.

Throwaway spike (ADR-0005 reversible; excluded from the cargo workspace). De-risks the
**dominant unknown** for ADR-0012 / `docs/design/keyhole-instrument.md`: the
**Rust→QML file-poll seam** and the Plasma-6 tray plasmoid that hosts it.

> Verdict up front: **VIABLE** to commit ADR-0012 to build — with one load-bearing
> correction to the design's assumed read mechanism (see finding #3). The file-poll seam,
> the three representations, the install path, and the honesty/UNKNOWN rendering are all
> proven by running; the in-panel tray visual is install-validated + doc-confirmed.

---

## What this builds

A Plasma 6 system-tray plasmoid (`org.agentos.keyhole`) that reads a JSON file written by
another process (`agentosd`, later) and re-renders live, with:

- `compactRepresentation` — the tray glyph (SHAPE per state, idle-vanish via `Plasmoid.status`).
- `fullRepresentation` — the **arbitration-led** instrument panel (lease/preempt first, then
  VRAM / residency / throughput), a 2px horizon strip, native KDE blurred popup.
- A **`Timer`-driven poll** of `keyhole.json` (NEVER one-shot sync-XHR — proven stale).
- First-class **UNKNOWN** (em-dash readouts, "can't reach Hermes"), never a fabricated `0`.

## Files

```
metadata.json                     Plasma 6 plasmoid manifest (tray-capable, SystemServices)
keyhole.json                      sample feed + the pinned schema-1 contract
mutate.sh                         cycles the feed idle→working→needs_you→acting→snag→unknown
contents/ui/main.qml              PlasmoidItem root: 3 reps + Plasma5Support reader + status
contents/ui/CompactRepresentation.qml   tray glyph
contents/ui/FullRepresentation.qml       the arbitration-led panel (shared w/ harness)
contents/ui/KeyholeModel.qml      state + the pluggable Timer-poll (shared w/ harness)
contents/ui/HorizonStrip.qml      the 2px Aurora-palette strip (never-red)
contents/ui/StateToken.qml        contrast-locked SHAPE+TEXT status token
harness.qml                       standalone qml6 window — proves the poll w/o a plasmoid viewer
polltest.qml / contracttest.qml   headless exit-code-gated proofs of the seam
runtests.sh                       orchestrates the live-update contract proof
```

## How to run it

### As a plasmoid (real tray integration)

```sh
# Install for the current user, then add "AgentOS Keyhole" via right-click → Add Widgets,
# or drag it into the system tray's configure dialog.
kpackagetool6 --type Plasma/Applet --install .
# update after edits:
kpackagetool6 --type Plasma/Applet --upgrade .
# remove:
kpackagetool6 --type Plasma/Applet --remove org.agentos.keyhole
```

There is **no `plasmoidviewer`** on this box (Plasma ships it in a separate package). The
install path is validated (`kpackagetool6` accepts the package, rc=0); the in-panel render
is therefore doc-confirmed against shipped Plasma-6 tray plasmoids + install-validated, not
screenshotted in a live panel.

### As a standalone harness (proves the file-poll seam — what we actually ran)

```sh
# The harness uses the XHR read path, which needs the Qt file-read override (see finding #3).
QML_XHR_ALLOW_FILE_READ=1 qml6 harness.qml -- /tmp/keyhole-live.json
# in another terminal, drive it:
DWELL=2 ./mutate.sh loop /tmp/keyhole-live.json
```

The plasmoid itself does NOT need that env var — it reads via `Plasma5Support.DataSource`
(`cat`), which works with no flag. The harness uses XHR only because it has no Plasma host.

### Headless proof (exit-code-gated; no display capture needed)

```sh
./runtests.sh           # writes working→(2.5s)→unknown, asserts the poll picks up the change
                        # exit 0 = PASS (live update + UNKNOWN honesty held); 30 = STALE
```

---

## The pinned contract — `keyhole.json` (schema 1)

This is a **new, separate** producer file — NOT a widening of the 4-scalar `agent.json`
wallpaper contract (ADR-0012 §2). Atomic temp+rename. `tokens_per_sec: null` means UNKNOWN
and is rendered as an em-dash — **never synthesized to 0**.

```json
{
  "schema": 1,
  "state": "working",            // idle | working | needs_you | acting | snag | unknown
  "gateway": "running",          // running | starting | degraded | stopped | unknown
  "floats": { "busy": 0.85, "warm": 0.0, "snag": 0.0 },
  "fleet":  { "running": 3, "queued": 2, "snagged": 0 },
  "lease":  { "tier": "interactive", "holder": "Hermes",
              "preempt": "wallpaper yielded ~1.5GB -> qwen2.5 loaded" },
  "vram":   { "used_mib": 6240, "total_mib": 8192 },
  "residency": [ { "name": "qwen2.5:14b", "loaded_secs": 240 } ],
  "tokens_per_sec": null
}
```

**Refinements made during the build (adjust ADR-0012's starting schema accordingly):**

- **UNKNOWN sentinels are negative, not zero.** `fleet.running:-1`, `vram.used_mib:-1`,
  `total_mib:-1` mean "no datum" and render as em-dash. A real `0` (e.g. `fleet.running:0`
  at idle) is a true value and renders as `0`. This is the honesty fix made concrete — the
  consumer must distinguish "no number" from "the number is zero", and JSON `null`/`-1`
  carry that where a bare `0` cannot.
- **`gateway:"unknown"` is the primary UNKNOWN driver**, ANDed with feed-mtime staleness in
  the consumer (`KeyholeModel.effectiveState`). Either one forces the UNKNOWN look, so a
  dead producer (stale file) and a reachable-but-blind producer (`gateway:"unknown"`) both
  surface honestly.
- **`lease` leads.** Empty `tier`/`holder`/`preempt` strings render as em-dash or
  "no contention"; the panel always allocates the ARBITRATION block first.

A serde round-trip test on this exact string + the `schema` field is the producer/consumer
pin (ADR-0012 §2) — to be added in the agentosd producer, mirroring `feed.rs:344`.

---

## Findings

### Environment

| tool | version |
|------|---------|
| plasmashell | **6.6.5** |
| Qt / qml6 (`Qml Runtime`) | **6.11.1** |
| kpackagetool6 | 2.0 (installs Plasma/Applet, rc=0) |
| qml5 (`qml`) | 5.15.19 (present, unused) |
| `plasmoidviewer` / `plasmoidviewer6` | **NOT installed** |
| spectacle | 6.6.5 (used for the visual capture) |
| session | live Wayland + Plasma (`wayland-0`, `XDG_RUNTIME_DIR=/run/user/1000`) |

QML import paths confirmed present: `org.kde.plasma.plasmoid` (`PlasmoidItem`),
`org.kde.plasma.core` (`PlasmaCore.Types.{Passive,Active,NeedsAttention,Hidden}Status`),
`org.kde.plasma.plasma5support` (`DataSource`), `org.kde.kirigami`, `QtCore`
(`StandardPaths`), `Qt.labs.platform`, `Qt.labs.folderlistmodel`.

### (2) Proved by running vs doc-confirmed

**Proved by running on this machine:**

- The **file-poll seam updates live** — `runtests.sh` / `contracttest.qml` exit `0`: the
  Timer-poll observed `working` then picked up an on-disk rewrite to `unknown` on a later
  tick (NOT stale), and held the UNKNOWN honesty contract (tok/s + VRAM em-dash).
- The **production read path** (`Plasma5Support.DataSource` executable `cat`, the mechanism
  `main.qml` actually uses) does the same live update with **no env flag** (`/tmp/dsloop.qml`
  proof, exit 0).
- The **full instrument renders correctly** — captured live: `working` shows the
  arbitration-led layout (LEASE `interactive (Hermes)`, PREEMPT the yield sentence), THROUGHPUT
  `—`, RESIDENCY `qwen2.5:14b · loaded 4m`, VRAM `6.1 / 8.0 GB` + bar, active board link-out;
  `unknown` shows `— Status unavailable — can't reach Hermes`, every readout an em-dash, board
  link-out disabled ("gateway unknown"). State→glyph+text mapping renders (◐ Working, ● needs,
  — unknown).
- `qmllint` clean on all components; `main.qml` resolves all Plasma imports; **`kpackagetool6`
  installs the package successfully** (rc=0).

**Doc-confirmed only (no plasmoidviewer to run the tray host):**

- The actual placement in the system tray, the idle-vanish hiding behavior of
  `Plasmoid.status = PassiveStatus`, the `NeedsAttentionStatus`→tray-attention behavior, and
  the native popup blur on the real `PlasmaCore` Dialog. These use the **exact APIs shipped
  plasmoids use** (verified against `org.kde.plasma.systemmonitor` and
  `org.kde.plasma.kdeconnect` on disk: `PlasmoidItem`, `compact/fullRepresentation`,
  `Plasmoid.status: connectedDeviceModel.count > 0 ? ActiveStatus : PassiveStatus`), and the
  package installs — but the in-panel visual was not screenshotted.

### (3) File-poll verdict — VIABLE, with a load-bearing correction

**The Timer-poll updates live. The sync-XHR staleness DID reproduce — and worse than expected:**

- **`XMLHttpRequest` on `file://` is DISABLED by default in this Qt 6.11**, for *both* sync
  and async, for *any* path form (absolute `file://`, relative-to-document, bare path). It
  returns empty — not stale-but-present, just empty. It only works with
  `QML_XHR_ALLOW_FILE_READ=1`, and **plasmashell does NOT set that env var** (verified via
  `/proc/<plasmashell>/environ`). So the design's assumed "Timer-poll re-creating the XHR"
  mechanism would silently read nothing inside a real plasmoid.
- **The fix, proven here:** read via **`Plasma5Support.DataSource`** (executable engine,
  `cat <path>`), re-issued each Timer tick. It reads the file with no flag, no security gate,
  and it is the same primitive shipped plasmoids already use. `main.qml` wires this as the
  `readBackend`; `KeyholeModel` stays host-agnostic (XHR fallback only for the harness).
- **Cadence:** proven at 300ms–1s in tests; the design's 2s idle / 5–10s-under-load is
  comfortably within reach. `DataSource` is async and one-shot-per-tick (disconnect on
  `onNewData`), so **reads never overlap** — which satisfies ADR-0012's "skip-on-stale, never
  overlapping" requirement for free.
- **Half-written-file safety:** the producer must temp+rename (`mutate.sh` does; `feed.rs`
  does). A malformed parse holds last-good and marks the tick unreachable — never blanks.

> This is the one finding that **must** flow back into ADR-0012: the read mechanism is
> `Plasma5Support.DataSource`, not `XMLHttpRequest`. It is a smaller, cleaner dependency than
> the design feared (Plasma5Support is already a plasmoid baseline), so it does not change the
> verdict — but a build that copied the design's literal "re-create the XHR" instruction would
> ship a keyhole that reads nothing.

### (4) Blur / translucency verdict — VIABLE, no custom shader

A `PlasmoidItem`'s `fullRepresentation` is hosted by Plasma in a `PlasmaCore` Dialog whose
background **is** the translucent, blurred panel-theme surface — the same native KWin blur the
panel and other tray popups get. The popup BACKGROUND needs no `ShaderEffect` / `FrostedGlass`
(ADR-0012 §7 satisfied by construction for the chrome). The harness paints its own quiet-dark
`#12141C` fallback only because it has no Plasma popup host; in the plasmoid that rectangle
sits *in front of* the blurred dialog background. Mechanism confirmed present
(`PlasmaCore` Dialog + the running compositor's blur); not separately screenshotted in-panel.

> **Update (2026-06-17):** the GLYPH PORTHOLE (`AuroraRing.qml`) now earns ONE scoped
> `ShaderEffect` — the real animated nimbus-aurora flow (`porthole.frag.qsb`, the same Flow
> look as the wallpaper + the status-panel backdrop), superseding §7's literal "no shader" for
> that single <100px surface while keeping its VRAM intent (see ADR-0012's 2026-06-17 amendment).
> Distinct from the popup-background claim above (the chrome is still shader-free). De-risked +
> integrated on the live session: `qml6 portholetest.qml` (mechanism: circular mask + 5 moods +
> t=2/10/20 motion → `portholetest.png`) and `qml6 ringtest.qml` (integration: glyph legible over
> the flow → `ringtest.png`). **Capture MUST run on the live session** — offscreen has no GL
> context, so a `ShaderEffect` grabs as a BLANK frame there (the trap to avoid). Shader compiled
> with `/usr/lib/qt6/bin/qsb --qt6` (the wallpaper qsb's target set).

### (5) Finalized `keyhole.json` schema

See "The pinned contract" above. Net change from the ADR-0012 starting point: **negative
sentinels for UNKNOWN numerics** (so a real `0` is distinguishable from "no datum"), and an
explicit note that `gateway:"unknown"` OR feed-staleness drives the UNKNOWN look.
`tokens_per_sec` stays `null`==UNKNOWN, never synthesized.

### (6) VIABLE / PARTIALLY-VIABLE / NOT-VIABLE

**VIABLE** to commit ADR-0012 to build. The dominant unknown (the Rust→QML file-poll seam)
is proven to update live, with a robust, flag-free read mechanism; the three representations,
the arbitration-led layout, the honesty/UNKNOWN rendering, the never-red snag, the idle-vanish
status API, and the package install are all confirmed. Calling it VIABLE rather than
"partially" because the tray half is doc-confirmed *and install-validated against the real
packaging tool and shipped-plasmoid APIs* — the only unproven step is the final mouse-drag into
a panel, which carries no remaining technical unknown.

**Deltas that remain (none block the commit):**

1. **Build the producer** — a sibling `agentosd keyhole` mode, near-clone of `feed.rs`
   (atomic temp+rename, adaptive cadence, the serde round-trip test pinning `schema`). The
   lease daemon must **push** state fire-and-forget **off the `Inner` lock** (ADR-0012 §3 /
   `lease.rs:290` pattern) — a render must never delay a SIGKILL. **(rust-performance-reviewer,
   resource-safety-reviewer)**
2. **Update ADR-0012's read-mechanism wording** from "Timer re-creating the XHR" to
   "`Plasma5Support.DataSource` executable read, re-issued per tick" — finding #3.
3. **Screenshot the in-panel tray** (idle-vanish, needs_you attention, popup blur) once a box
   with `plasmoidviewer` or a manual panel-add is available — to close the doc-confirmed gap.
4. **`acting` visual** is shipped as ▸ + text here, but the design notes its data is deferred;
   confirm the ▸ row look with **art-director / ambient-embodiment-reviewer**.
5. **Reduced-motion** is wired (clamps the tweens via `Kirigami.Units.longDuration <= 1`);
   the `aria-live`-on-transitions-only accessibility canon and the staggered-task motion are
   not yet exercised — fold in with **ambient-embodiment-reviewer**.
6. **tokens/sec stays UNKNOWN** until the ADR-0002 proxy lands (P2); approve/pause/cancel stay
   absent (no v1 write-path), per ADR-0012 §6.

---

## Notes for whoever builds the real thing

- `KeyholeModel.qml`, `FullRepresentation.qml`, `HorizonStrip.qml`, `StateToken.qml` are
  **dependency-light on purpose** (QtQuick + Layouts only) so the harness and the plasmoid
  share one render/state path. Keep the Plasma-only bits (`PlasmoidItem`, `Plasma5Support`,
  `StandardPaths`) in `main.qml`.
- The horizon strip is the **only** color (zero-GPU gradient + a 2.5s color tween). Its palette
  function has **no red path** — snag desaturates toward luma and dims; warm is the single
  reserved dawn-glow `rgb(255,153,87)`. Don't add a red.
- `tokString()` / `vramString()` / `fleetString()` are the honesty chokepoints: every one
  returns an em-dash under UNKNOWN or a negative sentinel. Add to them, don't bypass them.
