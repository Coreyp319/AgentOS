# AgentOS instrument tokens — the shared "glass" register

AgentOS's *instrument* surfaces (the things the substrate uses to **show you the truth** —
the keyhole tray, the status panel, future resource/lease readouts) wear one coherent
deep-navy glass skin, deliberately distinct from the light WhiteSur app chrome. Because
those surfaces are built in different technologies (QML plasmoid vs. an HTML page) they
can't share a runtime token file — so this doc is the **canonical source of truth**. When
you re-theme the instrument register, change the values here and mirror them into each
surface's token block.

Consumers:
- `spikes/keyhole/contents/ui/InstrumentPalette.qml` (the two-register skin), consumed by
  `StateToken.qml` / `AuroraRing.qml` (the glyph + its aurora halo) and `FullRepresentation.qml`
  (the panel + the VRAM aurora gauge) — ADR-0012
- `integrations/status-panel/panel.html` (`:root`, the `--inst-*` block)

## Palette (canonical)

| Token | Value | Role |
|---|---|---|
| `--inst-base` | `#12141c` | panel base / deepest background |
| `--inst-deep` | `#161a28` | mid background |
| `--inst-horizon` | `#1a2238` | top-of-gradient "horizon" |
| `--inst-text` | `#e6e9f0` | primary text |
| `--inst-muted` | `#8a90a0` | secondary text |
| `--inst-label` | `#878c9b` | quiet labels / captions (measured 4.5:1 on the glass — was `#7a8090`/3.86:1, lifted per ADR-0019 G7 + ADR-0031) |
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

## Aurora ramp (cool) — the GPU-pressure dawn

The glyph ring/halo (`AuroraRing.qml`) and the VRAM gauge fill (`FullRepresentation.qml`) wear
the **cool half** of the nimbus-aurora dawn — indigo → blue → violet — so the instrument breathes
the same palette as the reactive wallpaper. It is spent on **GPU pressure / activity only**; the
reserved warm is *never* mixed in here, keeping "pressure" (cool) visually distinct from "needs
you" (warm). It is **earned**: invisible/flat at rest, blooming and brightening with `busy`
(ADR-0012's "density-grows-with-load"). Zero-GPU — a QML gradient + a colour/width tween, no
shader, no Canvas (ADR-0012 §7, the same VRAM-coexistence reason the horizon strip obeys).

| Token | Dark | Light | Role |
|---|---|---|---|
| `auroraLo`  | `#27306E` | `#3E50C4` | deep indigo base (gauge low / halo floor) |
| `auroraMid` | `#4A5AD2` | `#5E54C2` | blue (gauge mid) |
| `auroraHi`  | `#8A6BDC` | `#7E42AE` | violet crest (gauge cap) |

The glyph **ring** samples the live `KeyholeModel.horizonColor` directly (so the ring, the 2px
horizon strip and the gauge share one breathing colour), darkened in the light register to stay
legible; the **gauge** uses the cool ramp above, lifted by `Qt.lighter(…, 1 + 0.3·busy)`.

## Light register (scheme-reactive)

The dark deep-navy values above are the **canonical** instrument skin. The keyhole
plasmoid now ALSO follows the desktop light/dark toggle (per user direction — the
"react to the taskbar toggle" requirement overrides the original always-dark intent):
its QML host (`main.qml`) derives `dark` from `Kirigami.Theme.backgroundColor`
luminance and injects an `InstrumentPalette` (`contents/ui/InstrumentPalette.qml`,
QtQuick-only so the dependency-light reps and the harness consume it without Kirigami)
that swaps to a light frosted register. The reserved warm *glow/halo* (`warm`) is held
identical in both registers; its *foreground* text/glyph (`warmText`) and any other accent
that would lose contrast on a light surface are darkened (a same-family copper for warm).

| Token | Dark | Light | Role |
|---|---|---|---|
| `base` | `#12141c` | `#f1f3f8` | panel base |
| `deep` | `#161a28` | `#e6eaf3` | mid background |
| `text` | `#e6e9f0` | `#1a1f2c` | primary text |
| `muted` | `#b4bac8` | `#3c4356` | secondary text |
| `label` | `#878c9b` | `#5a6173` | quiet labels (dark lifted to ≥4.5:1; was `#7a8090`/3.86:1) |
| `dim` | `#8a90a0` | `#5a6173` | unknown / snag / idle (light nudged to ≥4.5:1) |
| `hairline` | `#262a36` | `#cfd5e2` | 1px separators |
| `tintHover` | `#1c2230` | `#dde2ee` | row hover |
| `blue` | `#7aa2ff` | `#2c57c9` | link / acting |
| `warm` | `#ff9957` | `#ff9957` | **RESERVED** needs-you GLOW/halo FILL (identical across registers) |
| `warmText` | `#ff9957` | `#a8480f` | **RESERVED** needs-you FOREGROUND (glyph + label) — light darkened to a copper of the SAME warm family because `#ff9957` on `#f1f3f8` is only 1.90:1; `#a8480f` clears AA (~5:1). The bright `warm` stays the glow/halo fill only |
| `stUp` | `#86b89a` | `#2c7a50` | healthy |
| `stAmber` | `#d9b45a` | `#8a6310` | transitional |

The web status panel (`panel.html`) is **not yet** scheme-reactive — it still wears
the canonical dark register only. If we want it to follow the toggle too, mirror this
light register into its `:root` with a `prefers-color-scheme: light` block.

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

## Section wash (ember) — condensed-row earned motion

Motion is the off-nominal channel on the condensed SYSTEM rows (ADR-0012 §7 amendment, the
contained ember). A collapsed **attention** section carries a translucent warm wash **derived from
`warm` (#FF9957) — never a literal** — gathering from the row's low edge (top stop alpha 0 → bottom
stop alpha 1), scaled by the opacity envelope below. It **blooms in once on arrival, then holds a
steady glow — no sustained breath** (product-owner call), so a SYSTEM `attention` is motionless at
rest and never impersonates the wallpaper's reserved `needs_you` dawn-*breath*. Consumed by
`FullRepresentation.qml` (the board delegate).

| token | value | meaning |
|---|---|---|
| `wash-rest` | `0.09` | steady hold opacity (the level it settles to after the bloom) |
| `wash-peak` | `0.14` | one-shot bloom-in crest **and** the single reduced-motion still value |
| `wash-bloom-ms` | `1400` (OutCubic) | bloom-in on the transition *into* attention (the `AuroraRing` curve) |
| `tint-sunrise-ms` | `2500` (OutCubic) | header tint dawn-in on a real tone change (the HorizonStrip curve); gated by the `_prevTones` latch so a steady poll never re-fires it |
| `caret-rotate-ms` | `120` (OutCubic) | disclosure caret rotation on toggle (caret leads, tint follows) |
| `member-fade-ms` | `180` (OutCubic) | member rows fade on expand/collapse (opacity only — height is never tweened) |

Opacity envelope: `washOpacity = reducedMotion ? wash-peak : wash-rest + (wash-peak − wash-rest)·bloom`, where `bloom` ∈ [1→0] decays once over `wash-bloom-ms` on arrival and is 0 thereafter (steady at `wash-rest`). **Reduced-motion** holds at `wash-peak` (a legible still crest) with no bloom. **Honest UNKNOWN:** a stale/unreachable board performs none of this — rows go dim-still.
