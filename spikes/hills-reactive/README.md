# Spike: Hills reacts to `agent.json`

Validates the ambient **reactive grammar** from [`docs/vision.md`](../../docs/vision.md)
end-to-end on the *real* aurora shader: a hand-written agent-state file drives the
Nimbus Aurora **Hills** wallpaper (style 1) to a design-grammar-correct look per state.

## Result (proven)

| state | agent.json | what Hills does |
|---|---|---|
| idle | `busy:0,warm:0,snag:0` | baseline — calm cool ridgelines |
| working | `busy:0.85` | faster parallax + focus breath, a touch brighter/sharper |
| needs_you | `warm:0.90` | a slow **warm** dawn glow behind the far ridges (the ONE warmth) |
| snag | `snag:0.90` | thicker haze, desaturated, dimmer — "stopped, waiting" (never red) |

Captures: `hills_{idle,working,needs_you,snag}.png` (fixed `iTime`, so they differ
ONLY by agent state). Idle is byte-identical to the unmodified shader (all `uAgent*` = 0).

## The shader change

`aurora.frag` (copied from the pack) gains 4 uniforms in the std140 block
(`uAgentState` + `uAgentBusy`/`uAgentWarm`/`uAgentSnag`) and ~15 lines wired **only**
into the `style == 1` (Hills) branch — reusing existing knobs: the shared `flow`
advection + `focus` breath (working pace), `tex`/`shade` (working brightness/bloom),
an additive warm low-glow (needs_you), and a haze-mix + luma-desaturate + dim (snag).
Compiles with the pack's own recipe: `qsb --qt6 -o aurora.frag.qsb aurora.frag`.

## Run

```bash
./run.sh        # writes agent.json + agent_data.js per state, renders each to a PNG
```

## Findings → for the real implementation

- **Feed uniforms via a QML poller, not XHR.** `qml6`'s sync `XMLHttpRequest` on a
  relative file was unreliable (silently left uniforms at 0). The real `agent.json`
  bridge should poll + low-pass into the ShaderEffect uniforms exactly like the
  existing music/window feeds (`uMusicReact`/`uActiveMove`). This spike uses a
  generated `agent_data.js` shim only to load deterministically under a bare `qml6`.
- **Render on the session, not offscreen.** `QT_QPA_PLATFORM=offscreen` has no GL
  context here and produced blank frames.
- **Tuning:** the warm-glow vertical position and all gains are single constants in the
  Hills branch — easy to dial. Per the design, contributions stay capped + behind the
  highlight guard so working + loud music can't compound into a blowout.

Throwaway spike; the real change lands in the pack's `9-gpu-effects/interactive-bg/`.
