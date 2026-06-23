# Aurora panel — violet Plasma shell (panel · tray · plasmoids)

The shell half of the Aurora desktop look. `aurora-theme` re-accents Qt apps and GTK apps to
the wallpaper-derived **violet**; this component does the same for the **Plasma shell** — the
panel, system tray, plasmoids and applets — which otherwise keep the stock WhiteSur **blue**
because the desktop theme ships its own `colors` file that overrides the system colour scheme.

Without it, the always-on panel stays blue under your violet windows — the single most
identity-breaking seam in the OS-wide cohesion review (2026-06-23). With it, the whole desktop
reads as **one violet accent**.

## How (reversible, single source of truth)
`apply.sh` clones the active Plasma desktop theme to `<name>-aurora`, replaces that clone's
`colors` with `aurora-theme/color-schemes/AuroraDark.colors` (so the violet is defined in exactly
**one** place), and repoints `plasmarc` at the clone. Your original theme is never modified.

```sh
./apply.sh      # clone + repoint the shell at the violet clone   (apply aurora-theme first)
./restore.sh    # repoint the shell back to the original theme    (clone left inert; prints purge cmd)
```

## Notes
- **Opt-in / off by default** in `integrations/components.conf`. Pairs with `aurora-theme`
  (apply that first — this reuses its `AuroraDark.colors` and its `$STATE` revert home).
- **Effective at next login** (or restart plasmashell) — `plasmarc` is read at shell start.
- **Verify on screen at relogin** (the authoring harness can't render): hovered task-button /
  tray-selection highlight should read **violet**, not blue. If a WhiteSur SVG hardcodes a blue
  fill outside the `ColorScheme-*` classes, a stray blue could remain — that's the one thing to
  eyeball; `restore.sh` reverts instantly if so.
- ADR-0042 (OS-cohesion amendment).
