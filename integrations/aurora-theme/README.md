# Aurora theme — AgentOS desktop look (Union CSS + light/dark schemes)

The repo-tracked, reversible home of the AgentOS desktop widget identity: a **Union CSS
style** (`aurora`) that re-expresses the WhiteSur look, plus a **cohesive light/dark
colour-scheme pair** (`Aurora Dark` / `Aurora Light`) carrying the lavender accent. This is
the productionized form of the manual Union loop in ADR-0042 — promoted out of `~/.local`
so it survives a reinstall.

> **Union is a Plasma 6.7 tech preview.** This component is **opt-in / off by default** in
> `integrations/components.conf`. Everything here is reversible (`restore.sh`).

## The two layers (and why)

| Layer | What it carries | Files |
|---|---|---|
| **Shape / spacing / depth** | The WhiteSur re-expression. *Mode-agnostic* | `css/styles/aurora/*.css` |
| **Colour (light vs dark)** | The lavender identity in each mode | `color-schemes/Aurora{Dark,Light}.colors` |

One Union style serves **both** modes because every colour binds through
`custom-color("kcolorscheme", …)` — it follows whatever scheme is active. Union 6.7 exposes
**no "is-dark" selector to CSS**, so the only honest place for light/dark is the colour
scheme. Switching modes = swapping the scheme (`aurora-mode.sh`).

## The look (measured from the user's NimbusRefinedDark/WhiteSur pack)
- **Two-radius scale** — `--corner-radius: 6px` on inline controls/inputs, `--popup-corner-radius:
  12px` on every *floating* surface (menu, popup, dialog, tooltip, card). That control-vs-card
  contrast is the WhiteSur signature, not the absolute radius.
- **Soft floating lift** — a single, large-blur drop shadow (Union supports only one shadow
  layer — no comma stacks, no `inset`), backed by the scheme-bound 1px hairline border so the
  float still reads on dark backgrounds where a black shadow would vanish.
- **Lavender focus ring** (`--focus-outline-alpha: 0.45`) — the one accent that ties it together.
- **Generous menu gutter.**

## Colour identity
- **Aurora Dark** — a violet *elevation ladder* (Complementary < View < Window/Header < Tooltip
  < Button), violet accent `118,92,196`, lavender focus `199,189,229`. (Was the user's
  `CoreyLavender`; rebranded, with one semantic foreground nudged to clear WCAG AA.)
- **Aurora Light** — the cohesive twin: the *same* violet accent, a deep-violet focus ring, the
  dark ladder mirrored into a faint-lavender near-white ramp, semantics darkened for white.
- **Shader-derived accent.** The violet is not arbitrary — `tools/derive-accent.py` rotates the
  charter violet a bounded 35% toward the idle wallpaper's resting hue (the Indigo Channel fog,
  RGB 39,41,69), **hard-clamped to a violet arc [255–285°]** with a WCAG luminance floor, so the
  chrome reads *co-lit by the environment* yet can never leave violet or drop below AA. Re-run it
  to reproduce the accent; pinned to the Indigo Channel as the canonical home wallpaper.
- Both verified **WCAG AA** (most pairings AAA) by `tools/check-contrast.py` — body, selection,
  link, semantics, **and** the focus + hover rings (non-text 3:1).

## Use
```sh
./apply.sh                 # install style + schemes, activate Union, default to Aurora Dark
./aurora-mode.sh           # toggle light ⇄ dark
./aurora-mode.sh light     # or set explicitly
./restore.sh               # revert widgetStyle + scheme + env knob (files left inert)
preview/gallery.sh both    # one-window control gallery — BEFORE (breeze) + AFTER (aurora) side by side
tools/check-contrast.py color-schemes/Aurora*.colors   # re-verify WCAG after any scheme edit
tools/derive-accent.py     # re-derive the accent from the wallpaper's idle hue (prints the numbers)
```
- The **full session restyle** (the `aurora` CSS) needs the `UNION_STYLE_NAME` env knob, which
  is effective at **next login**; until then the QtWidgets style is Union but the CSS is stock
  breeze. The colour-scheme swap (`aurora-mode.sh`) applies live.
- Author/iterate the CSS with the `union-css-theming` skill's `css-tx.py` (gated checkpoint/revert).

## Gotchas (see the `union-css-theming` skill REFERENCE for the full set)
- A custom Union style needs the `defaults/` mirror in the user data root or Union throws on
  load — `apply.sh` copies it from the installed engine (so it matches the version, since the
  dialect can shift between releases).
- Never set `QT_QUICK_CONTROLS_STYLE=org.kde.union` globally — it leaks into Flatpak sandboxes.
  `UNION_STYLE_NAME` (what `apply.sh` writes) is Flatpak-safe.
- The name **`aurora`** intentionally overlaps the `nimbus-aurora` reactive-wallpaper feed —
  the ambient identity (wallpaper + widgets) is unified under one name by design.

## Review follow-ups (see ADR-0042 Amendment, 2026-06-22)
Three panels reviewed this — cohesion 7/10, a11y 5/10 (→ blocker fixed), vision-fit 8/10. Must-fixes
are in: solid focus ring (the translucent ring failed WCAG 2.4.13), the `--elevation` ramp, and the
selection alt-band. Open, **verify on screen at relogin** (GUI render isn't available in the authoring
harness; every item below is a single-token, reversible edit):
- **CVD redundancy** — links vs body/visited and the positive/neutral/negative semantics lean on hue;
  the robust fix is underline / icon shape at the *app (Kirigami)* layer, not the theme.
- **Interaction states** — dark button hover is border-only (~2:1), checked ≈ pressed fills, switch
  off-handle is faint.
- **Elevation-ladder mirror** — dark `View` is the recessed "well", light `View` is the brightest paper
  (floating surfaces still lift via the ramp + hairline in both). Raising dark `View` above `Window` is
  a one-token change if you prefer a true mirror.
- **Dark hairline** is soft at `contrast=4`; a contrast bump sharpens it but trades against "calm".
