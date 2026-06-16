# AgentOS instrument tokens — the shared "glass" register

AgentOS's *instrument* surfaces (the things the substrate uses to **show you the truth** —
the keyhole tray, the status panel, future resource/lease readouts) wear one coherent
deep-navy glass skin, deliberately distinct from the light WhiteSur app chrome. Because
those surfaces are built in different technologies (QML plasmoid vs. an HTML page) they
can't share a runtime token file — so this doc is the **canonical source of truth**. When
you re-theme the instrument register, change the values here and mirror them into each
surface's token block.

Consumers:
- `spikes/keyhole/contents/ui/StateToken.qml`, `FullRepresentation.qml` (QML — ADR-0012)
- `integrations/status-panel/panel.html` (`:root`, the `--inst-*` block)

## Palette (canonical)

| Token | Value | Role |
|---|---|---|
| `--inst-base` | `#12141c` | panel base / deepest background |
| `--inst-deep` | `#161a28` | mid background |
| `--inst-horizon` | `#1a2238` | top-of-gradient "horizon" |
| `--inst-text` | `#e6e9f0` | primary text |
| `--inst-muted` | `#8a90a0` | secondary text |
| `--inst-label` | `#7a8090` | quiet labels / captions (clears WCAG 4.5:1 on the glass) |
| `--inst-blue` | `#7aa2ff` | cool accent, links, "acting" |
| `--inst-warm` | `#ff9957` | **RESERVED** — the "needs you" dawn-glow cue. Do not spend on decoration. |
| `--brand-warm` | `#e0884f` | lower-saturation copper for brand decoration, so the reserved hue stays exclusive |
| `--glass` | `rgba(38,42,54,0.46)` | frosted card fill (solid `rgba(24,28,40,0.94)` when transparency is reduced) |
| `--hairline` | `rgba(255,255,255,0.07)` | 1px separators |

## Status ramp (derive, don't hard-code)

Five live hues; the **fg** (text-on-glass), **ring** (halo) and **line** (border) variants
are *derived* with `color-mix` so re-hueing a base carries its whole family — no satellite
literals to drift.

| Base | Value | Meaning |
|---|---|---|
| `--st-up` | `#74d39a` | running / ready / done |
| `--st-amber` | `#f2c879` | starting · stopping · warn |
| `--st-red` | `#ec7676` | failed · live-but-unreachable |
| `--st-idle` | `#757c8e` | down / absent (≥3:1 as a graphical mark) |
| `--st-unknown` | `#6f7894` | can't determine (dashed ring) |
| `--st-acting` | = `--inst-blue` | reserved: computer-use "acting" |

## Dot grammar (3 orthogonal axes)

- **Fill** — solid = a process is/was present; hollow = no live process (`ok`, `absent`, `unknown`).
- **Glow** — reserved for *attention only* (`failed`, up-but-unreachable). Never decorative.
- **Stroke** — separates the hollow trio: `ok` = solid green ring, `absent` = solid idle ring,
  `unknown` = dashed `--st-unknown` ring.

## Scales

- **Type:** `rem`-based modular scale (`--fs-display` 1.19 → `--fs-3xs` 0.656), three weights
  (`--fw-regular/medium/semibold`). `rem` so it honors user font-size and Plasma HiDPI scaling.
- **Space:** 4px base (`--sp-1`=4 … `--sp-10`=64).
- **Radius:** `--radius-sm` 9 / `--radius-md` 16 / `--radius-pill` 999.
- **Blur/glass:** `--blur-raised` 14 / `--blur-overlay` 18, with a no-blur fallback under
  `prefers-reduced-transparency` for the graphics-yield / low-VRAM case.
