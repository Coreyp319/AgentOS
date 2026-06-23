# swaync-aurora — AgentOS "instrument glass" notification style

A calm, deep-navy, single-violet-accent [SwayNotificationCenter](https://github.com/ErikReider/SwayNotificationCenter)
stylesheet that unifies the notification "nervous system" with the rest of the
AgentOS instrument register (the keyhole tray, the status panel).

## What it does

Installs a complete `style.css` into `~/.config/swaync/` that re-skins toasts,
the control center, action buttons, DND, and the MPRIS widget in the AgentOS
look. It **replaces a macOS-mimic default** (Apple blue `#0a84ff`, Apple green
`#34c759`, SF Pro font) with deep-navy frosted glass, Inter, and ONE violet
accent — matching `integrations/design/instrument-tokens.md`.

- **Off by default / opt-in.** Nothing changes until you run `apply.sh`
  (the parent registers this component in `components.conf`).
- **Reversible** (ADR-0005). `apply.sh` backs up your existing style once to
  `style.css.preaurora`; `restore.sh` puts it back exactly.
- **Never launches swaync.** Scripts only hot-reload CSS *if swaync is already
  running*; otherwise they just install the file and tell you it takes effect
  on next swaync start.

## Token mapping (instrument-tokens.md → selectors)

| Token | Value | Where it lands |
|---|---|---|
| base | `#12141c` | control-center gradient bottom, inline-reply entry |
| deep | `#161a28` | notification card fill (`@ ~0.92` glass) |
| horizon | `#1a2238` | control-center gradient top, card hover |
| text | `#e6e9f0` | `.summary`, `.widget-title`, DND knob |
| muted | `#8a90a0` | `.body`, mpris subtitle, close-button glyph |
| label | `#878c9b` | `.time`, list placeholder |
| hairline | `rgba(255,255,255,0.07)` | card borders, separators, troughs |
| **ACCENT (violet)** | **`#9b82e0`** | **action buttons, links, focus ring, DND-on, progress, clear-all** |
| critical | `#ec7676` | `.notification.critical` border + close-button hover ONLY |
| warm (reserved) | `#ff9957` | **never used here** — reserved for "needs you" |

The accent was just unified from periwinkle/blue `#7aa2ff` → **violet `#9b82e0`**.
Red (`#ec7676`) is spent only on the critical/urgent state and the destructive
close-button hover. The reserved warm `#ff9957` is **not** used.

## Use

```sh
./apply.sh      # back up existing style → install Aurora → reload if running
./restore.sh    # restore the backup (or remove ours) → reload if running
```

Both are idempotent and fail-open. If swaync is not running, neither launches
it — the change takes effect on the next swaync start.

## Notes / things to eyeball on screen

- "Glass" is a translucent navy card + one soft drop shadow. KWin does not blur
  layer-shell surfaces, and GTK4 CSS has no `backdrop-filter`, so there is no
  real frost — the depth is alpha + shadow by design.
- swaync's selector set has drifted across releases. The DND toggle is matched
  via both `.widget-dnd > switch` and `.control-center-dnd`; the clear-all via
  both `.widget-title > button` and `.control-center-clear-all`. If a control
  looks unstyled after relogin, check your installed swaync version's selector
  names — they are the most likely drift point.
