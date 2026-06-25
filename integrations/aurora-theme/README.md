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
- **One lavender focus ring** — a SOLID `--focus-ring-color` (≥3:1, WCAG 1.4.11) on *every*
  keyboard-focusable control, buttons included; the `0.45` alpha lives only on the soft menu
  hover-fill, never on an outline. Enforced at install by `tools/lint-css.py` (solid-outline +
  focus-coverage).
- **One engaged-hover border** — `--control-hover-border-color` (DecorationHover, ≥3:1) on every
  control's `:hovered`, replacing the old sub-3:1 selection violet.
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
  link, semantics, the focus + hover rings (non-text 3:1), **and** the COMPUTED `mix()` colours the
  CSS synthesises (menu shortcut text, placeholder), which raw-key checks were previously blind to.

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

## Review follow-ups (see ADR-0042 Amendment)
Five-lens systematic review (2026-06-25, ~7/10 across visual-system / a11y / craft / layout /
reversibility) → a full remediation pass landed:
- **Done** — focus ring unified to one solid colour + extended to every focusable widget (was
  missing on dial/tabbutton/itemdelegate/card/chip/navigationtabbutton/calendar/headerview);
  the WCAG-safe hover border now reaches *all* controls (was 2 of ~8); menu shortcut text raised
  to ≥4.5:1 (was ~2.95:1 in light); `.changed` given a non-colour edge-bar; ~9 dead tokens swept;
  the pill/circle radius family + elevation shadow + type sizes tokenized; the dialog-title HiDPI
  bug fixed (fixed-px → relative). Gates hardened: `lint-css.py` (solid-outline + **focus
  coverage**), computed-`mix()` contrast checks, and a leak-free `restore.sh` (best-effort,
  env-knob round-trip, empty-scheme sentinel).
- **Open — verify on screen at relogin** (GUI render isn't available in the authoring harness, and
  Union 6.7's `ruleinspector` can't resolve `:state` rules — see the `union-css-theming` skill):
  the new `:visual-focus` / `:selected` rules parse + load clean but their *rendering* is
  unconfirmed.
- **Open — deferred by design**: CVD redundancy for links/semantics (underline/icon shape belongs
  at the *app/Kirigami* layer, not the theme); a handful of secondary controls below the 24px
  WCAG-2.5.8 target (spinbox steppers, chip-delete, bar handles — mostly exempt as inline/secondary,
  changing them distorts layout); the OFF-switch handle contrast; the elevation-ladder mirror
  preference (dark `View` as "well" vs raising it above `Window`); the dark hairline softness at
  `contrast=4`; and a keyboard `menuitem:highlighted` cue (deferred to avoid reintroducing the
  sticky-highlight the `popup.css` comment warns about).
