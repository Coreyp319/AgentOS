# integrations/window-drag-wind — the window-drag → wind producer (ADR-0023)

When you shove a window across the desktop, the reactive wallpaper gusts in that direction. This
is the **producer** half: a tiny, read-only KWin 6 script that derives a drag vector (direction +
speed) from window-MOVE geometry deltas and emits it over the session bus.

## The two halves

| half | what | where it ships |
|------|------|----------------|
| **producer** | this KWin script: per-step `frameGeometry` delta → throttled `Gust(dirX,dirY,speed,active)` on `org.agentos.Wind1` (session bus). Geometry only — no titles, identity, or content. | **this component** |
| **sink** | `crate::wind` — serves `org.agentos.Wind1` at `/org/agentos/Wind`, runs a 60 Hz deterministic spring, atomically writes `$XDG_RUNTIME_DIR/nimbus-aurora/wind.json`. Mounted on the lease daemon's `org.agentos.Coordinator1` connection (provably off the SIGKILL lock path). | **core-substrate** (the `lease` daemon) |

Install **core-substrate** for the sink; this component for the producer. Without the sink the
producer's `Gust` calls are harmless no-ops (fail-open, ADR-0003).

## Install / remove

Driver-managed (`id: window-drag-wind`, tier `desktop`):

```sh
integrations/install.sh   --only window-drag-wind     # add
integrations/uninstall.sh --only window-drag-wind     # remove
```

Or directly (user-scope, no root):

```sh
integrations/window-drag-wind/apply.sh
integrations/window-drag-wind/restore.sh
```

`apply.sh` installs the KWin script, enables it in `kwinrc [Plugins]`, and **hot-reloads KWin**
(`qdbus org.kde.KWin /KWin reconfigure`) — so it goes live with **no logout and no shell restart**
(a real advantage over the keyhole plasmoid). On a non-KWin host it skips cleanly. `restore.sh`
drops the enable key, uninstalls the package, reconfigures, and clears `wind.json`.

## Privacy (load-bearing)

The script reads **only** `frameGeometry` (x, y, w, h) deltas and `normalWindow`/`dock`/
`desktopWindow` flags (to decide *whether* to react). It never reads, stores, or emits a caption,
resource class, PID, window id, or content. The D-Bus payload is three floats + a bool. Verify by
inspection of `package/contents/code/main.js` — there is no code path that can serialize an identity.

## Contract

`Gust(d dirX, d dirY, d speed, b active)` on `org.agentos.Wind1` @ `/org/agentos/Wind`, served by
`crate::wind` (pinned by the `gust_clamps_renormalizes_and_rejects_nan` + `wind_path_takes_no_inner_lock`
tests in `crates/agentosd/src/wind.rs`). `wind.json` is schema 1 (sibling of `agent.json`).

## Owed

The live KWin→D-Bus round-trip during an interactive drag (does `interactiveMoveResizeStepped`
fire per-step on Wayland; does `callDBus` reach `org.agentos.Coordinator1` without a bus policy
file) needs a hands-on drag on the live session — see the spike's "Honest unknowns". The **sink**
side of the contract is verified live (calling `Gust` updates `wind.json`); the canonical producer
source and its feasibility findings remain in `spikes/window-drag-wind/`.
