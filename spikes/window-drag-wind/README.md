# Spike: window-drag → wind producer

> **Promoted.** The shipped producer now lives in `integrations/window-drag-wind/`
> (driver-managed, `id: window-drag-wind`); the sink (`wind_sink_sketch.rs`) shipped as
> `crates/agentosd/src/wind.rs`. This directory is kept as the **feasibility record + the
> canonical "Honest unknowns" list** (the live KWin→D-Bus drag round-trip). Edit the shipped
> package under `integrations/window-drag-wind/package/`, not here.

Prototype for **Design 0023-window-drag-wind-producer** (open questions 5 & 6 of
`docs/design/0023-creative-environment-pipeline.md`). A KWin 6 / Plasma 6 Wayland script that
turns the direction + speed of a window MOVE into a wind vector for the reactive scene shader.

Throwaway sandbox (`spikes/` is excluded from the cargo workspace). The real producer half is
the KWin package; the consumer half (`wind.json` sink) is sketched, not built — see the spec.

## Files

| File | What |
|---|---|
| `kwin-script/metadata.json` | KPackage manifest (`X-Plasma-API: javascript`, disabled by default) |
| `kwin-script/contents/code/main.js` | The producer — connects to `interactiveMoveResize*`, derives the drag vector from `frameGeometry` deltas, `callDBus`-es a raw `Gust(...)` to the session bus. Sandbox-honest (no file I/O). |
| `wind.schema.json` | The `wind.json` runtime-file contract (sibling of `agent.json`), versioned schema 1. |
| `wind_sink_sketch.rs` | REFERENCE ONLY — the deterministic spring + atomic write the agentosd `wind` sink would run. Not wired into the crate (a D-Bus server is a structural add — flagged in the spec). |
| `apply.sh` / `restore.sh` | Reversible install/uninstall (`kpackagetool6` + `kwriteconfig6` + `qdbus reconfigure`). |

## Run (on the LIVE Plasma 6 session — Wayland)

```bash
./apply.sh        # install + enable + hot-reload KWin
journalctl --user -f | grep agentos-wind     # see the load line
# drag a normal window; the script callDBus-es Gust(dirX,dirY,speed,active) per ~50ms step
./restore.sh      # full revert
```

With no sink serving `org.agentos.Wind1`, the `callDBus` calls are harmless no-ops (fail-open).

## Verified vs unverified

- **Verified (docs):** the Plasma 6 API names — `workspace.windowAdded`,
  `Window.interactiveMoveResizeStarted/Stepped/Finished`, `Window.frameGeometry`,
  `callDBus(...)` (session bus only), `readConfig`. See the spec's Sources.
- **NOT verified on a live KWin here:** I did not run `./apply.sh` against the live compositor
  in this session (no interactive drag harness, and modifying the live desktop is out of scope
  for the spec pass). The honest unknowns — does `interactiveMoveResizeStepped` fire per-step on
  Wayland or only at end under tiling; does `callDBus` reach a custom session-bus name without a
  policy file — are listed in the spec under "Honest unknowns."
