# Aurora notifications — native Plasma popups in the instrument-glass register

Now that **swaync is retired** and **plasmashell owns `org.freedesktop.Notifications`**, the
notification popups are the *native Plasma* toast. This component gives that toast the AgentOS
**Aurora "instrument glass"** look — the same deep-navy card + violet accent the retired
`swaync-aurora` style carried, so the notification "nervous system" keeps its skin across the
swaync→Plasma handover.

## What actually controls the popup look (verified, Plasma 6.7.1)

The popup is **custom Plasma QML** — a `PlasmaQuick::Dialog` rendered by plasmashell (the
applet QML is qrc-compiled into `plasma-workspace`, not loose on disk). It is **not** QtWidgets
and **not** generic QtQuick.Controls, so **Union CSS does not reach it.** Its appearance comes
entirely from the **active Plasma desktop theme**:

| Popup element | Driven by |
|---|---|
| **Chrome** — card body colour, corner radius (~11–12px), hairline, drop shadow | the theme's `dialogs/background.svgz` (a 9-patch SVG, shared by every floating Plasma dialog) |
| **Accent / text / action buttons** | the theme's `colors` file — which, because the desktop theme ships its own `colors`, **overrides the system colour scheme** for shell surfaces |

So two levers, two owners:
- **This component owns the chrome.** It swaps the card body from the stock neutral dark
  `#1a1a1a` to the Aurora **instrument navy `#161a28`** (exactly `swaync-aurora`'s
  `rgba(22,26,40,…)` toast). Radius, hairline (`#ffffff @ 0.06`) and the drop-shadow are kept —
  WhiteSur-dark's dialog already ships the ~11px float radius Aurora wants.
- **`aurora-panel` owns the accent.** It sets the cloned theme's `colors` to **Aurora Dark**, so
  the violet (`118,92,196`) replaces the loud WhiteSur blue (`49,91,239`) on the popup's focus
  ring and action buttons. Run it too for the full look; this component alone gives the navy glass.

## Light / dark

The **accent and text colours follow the active colour scheme** (Aurora Dark / Aurora Light) via
`aurora-panel`'s `colors` swap — so the popup tracks the mode you switch with
`aurora-theme/aurora-mode.sh`. The **chrome navy is a dark-register choice baked into the SVG**
(the instrument glass is intentionally a deep-navy card in both modes, matching the keyhole tray /
status panel instrument surfaces). If you want a light-mode card body, that's a one-line SVG
re-tint — call it out and it's a single follow-up.

## How (reversible, composable)

`apply.sh` reuses the active `<theme>-aurora` clone if the shell is already on one (i.e.
`aurora-panel` ran), else clones the active desktop theme. It backs up the clone's original
`dialogs/background.svgz` once (`$XDG_DATA_HOME/aurora-theme/dialog-background.svgz.preaurora`)
and installs the Aurora navy one. The original theme is never touched.

```sh
./apply.sh      # install the navy dialog chrome into the aurora clone, point the shell at it
./restore.sh    # restore the clone's original dialog chrome (prints the shell-repoint if needed)
```

## Notes
- **Opt-in / off by default** in `integrations/components.conf`. Pairs with `aurora-panel`
  (for the violet accent) and `aurora-theme` (for the Aurora Light/Dark schemes).
- **Effective at next login / plasmashell restart** — the desktop-theme SVG cache and `plasmarc`
  are read at shell start. (A live `plasmashell` restart picks it up too, but restarting the
  shell is disruptive; relogin is the calm path.)
- **Scope (honest):** `dialogs/background.svgz` is shared by all floating Plasma dialogs, so
  tray/applet popups and OSDs get the same navy glass. That is the intended Aurora instrument
  register, not a side effect — and `restore.sh` reverts all of it.
- **Verify on screen at relogin:** `notify-send -a AgentOS "Aurora test" "styled notification body"`
  → the toast should read as a deep-navy glass card (not the stock charcoal), with a violet (not
  blue) focus/action accent once `aurora-panel` is also applied.
- ADR-0042 (OS-cohesion amendment).
