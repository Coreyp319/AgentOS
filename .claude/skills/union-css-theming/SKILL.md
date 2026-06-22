---
name: union-css-theming
description: Playbook for restyling the AgentOS / KDE Plasma desktop UI via Union — the CSS-based Qt style engine that shipped as a tech preview in Plasma 6.7. Covers what Union is and how it differs from the SVG/Kvantum/QtQuick styling layers it unifies, installing + globally activating it (Application Style = widgetStyle, NOT the Flatpak-breaking env var), the real CSS authoring loop (custom named style under ~/.local/share/union/css/styles/<name>/ + the required defaults/ mirror, the UNION_STYLE_NAME / cache env knobs, union-ruleinspector to debug rule matching, per-app QtQuick vs QtWidgets test launches), the CSS dialect (selectors, states, :root tokens, custom-color/modify-color/mix functions, kcolorscheme color binding), and reversible activation. Use when authoring or editing a Union CSS style, theming a Plasma/AgentOS Qt or QtQuick surface, debugging why a Union rule doesn't match, activating/reverting Union globally, or planning AgentOS's CSS-based desktop personalization path.
---

# Union CSS theming (AgentOS desktop UI)

**Union** is KDE's new style engine (Plasma 6.7 tech preview): a three-layer pipeline —
**input** (`union-input-css.so`, reads CSS) → **intermediate** (`libUnion.so`, an abstract
element/property model) → **output** (`UnionWidgetsStyle.so` for QtWidgets, `org.kde.union`
QQC2 style for QtQuick). One CSS source styles **both** toolkits, which is why it can finally
unify Plasma's SVG-theme / Kvantum / QtQuick-style fragmentation. At 6.7 it renders ~identical
to Breeze and ships off by default — it is a **preview**, not yet a stable theming product.
Deep detail (env-var table, full CSS dialect, color system, troubleshooting): [REFERENCE.md](REFERENCE.md).

## State on this box (2026-06-22, Plasma 6.7.0)
- `union 6.7.0` installed (dep: `cxx-rust-cssparser`). System styles: `/usr/share/union/css/`.
- **Global Application Style = Union** (`kdeglobals [KDE] widgetStyle=Union`; was `kvantum`).
  Revert: `kwriteconfig6 --file kdeglobals --group KDE --key widgetStyle kvantum` + relog.
- Editable AgentOS style scaffolded at `~/.local/share/union/css/styles/agentos/` (copy of
  breeze) **plus** the required `~/.local/share/union/css/defaults/` mirror (see gotcha below).

## The authoring loop
```sh
STYLE=agentos
DIR="$HOME/.local/share/union/css/styles/$STYLE"

# 1. EDIT the CSS (start from the breeze copy already scaffolded).
$EDITOR "$DIR/variables.css"   # design tokens: --corner-radius, --grid-unit, colors …
$EDITOR "$DIR/button.css"      # per-widget rules

# 2. INSPECT which rules/properties resolve for a widget+state (no GUI, fast).
UNION_STYLE_NAME=$STYLE union-ruleinspector --style org.kde.union \
    --type Button --state hovered            # --state repeatable: hovered, pressed, checked, disabled…

# 3. LIVE-TEST one app, cache-bypassed so edits show immediately:
#    QtQuick (Plasma/QML apps) — Union's native QQC2 style:
UNION_STYLE_NAME=$STYLE UNION_DISABLE_STYLE_CACHE=1 \
    QT_QUICK_CONTROLS_STYLE=org.kde.union  systemsettings &
#    QtWidgets apps — Union's QStyle:
UNION_STYLE_NAME=$STYLE UNION_DISABLE_STYLE_CACHE=1 QT_STYLE_OVERRIDE=Union  dolphin &

# 4. GLOBALIZE the custom style (optional). widgetStyle=Union already covers QtWidgets +
#    qqc2-desktop-style-bridged QML; to make the session load the *agentos* CSS instead of
#    breeze, set the style name in the session env (UNION_STYLE_NAME is ignored inside
#    Flatpak sandboxes, so it is Flatpak-safe — unlike QT_QUICK_CONTROLS_STYLE):
mkdir -p ~/.config/environment.d
printf 'UNION_STYLE_NAME=%s\n' "$STYLE" > ~/.config/environment.d/union-style.conf   # effective next login
```

## Safe edits — `css-tx.py` (checkpoint / revert, with a validated gate)
The reversible apply/rollback rail for the manual loop (mirrors the ADR-0034 audit's
`backup_once`/`do_revert`, adds a deterministic gate). Edit `agentos/*.css` freely, then:
```sh
SKILL=~/Documents/AgentOS/.claude/skills/union-css-theming
python3 $SKILL/css-tx.py verify       # the gate: union-ruleinspector loads the style? (no throw)
python3 $SKILL/css-tx.py status       # valid? dirty vs last checkpoint? which files changed?
python3 $SKILL/css-tx.py diff         # unified diff: working tree vs last checkpoint
python3 $SKILL/css-tx.py checkpoint --note "round corners + lavender focus"   # REFUSES if invalid
python3 $SKILL/css-tx.py revert        # restore the last good checkpoint (or --to <run_id>)
python3 $SKILL/css-tx.py history       # the append-only ledger
```
The whole point: `checkpoint` runs the `union-ruleinspector` gate first and **refuses to bless a
state that doesn't load** (code disposes), so `revert` always lands on a validated checkpoint — no
opaque shadow-write, no login-revert "lie" (ADR-0034). Checkpoints + ledger live in
`~/.local/share/union/css/.tx/`; each snapshots both the style dir and the required `defaults/`.
Style defaults to `$UNION_STYLE_NAME` or `agentos`.

## Non-negotiable gotchas
- **Mirror `defaults/` into the user data root.** A style under `~/.local/share/union/css/styles/<name>/`
  makes Union resolve `defaults/default.css` relative to *that* data root
  (`~/.local/share/union/css/defaults/`). If it's missing, Union **throws** (`rust cxxbridge … IO
  Error … defaults/default.css`). Already mirrored here; recreate with
  `cp -r /usr/share/union/css/defaults ~/.local/share/union/css/defaults`.
- **Never set `QT_QUICK_CONTROLS_STYLE=org.kde.union` globally** (env.d, /etc/environment, profile).
  It leaks into Flatpak sandboxes, where the Union QML style isn't present → Flatpak QML apps break.
  Use it only per-app launch. Globalize the *widget* style via `widgetStyle` (kdeglobals, not env)
  and the *named CSS* via `UNION_STYLE_NAME` (Flatpak-safe).
- **Cache.** Union caches the compiled style; edits won't show until you bypass it
  (`UNION_DISABLE_STYLE_CACHE=1`) or force a rebuild (`UNION_FORCE_RECREATE_STYLE_CACHE=1`).
- **Preview maturity.** 6.7 Union ≈ Breeze; coverage is incomplete and the CSS dialect/selectors
  can change between releases. Treat custom styles as experimental, not a shipping AgentOS dependency.

## The CSS dialect (1-minute orientation)
Real CSS with `@import`, `:root` custom-property tokens, type/id/state selectors, and Union
functions for KDE color binding. From `styles/breeze/`:
```css
:root {                          /* variables.css — tokens shared by all rules */
    --corner-radius: 5px;
    --grid-unit: 18px;
    --highlight-color: custom-color("kcolorscheme", "active", "selection", "background", "normal");
    --button-active-highlight-color: mix(var(--button-active-background-color), var(--highlight-color), 0.333);
}
```
- `custom-color("kcolorscheme", set, role, ground, variant)` — bind to the live KColorScheme
  (so the style follows the user's color scheme instead of hard-coding hex). `modify-color(c set-alpha 0.3)`,
  `mix(a, b, t)`, `var(--token)` round out the toolkit.
- `style.css` is the entry point: it only `@import`s the per-widget files (`button.css`,
  `popup.css`, `delegate.css`, `text.css`, `check.css`, `bars.css`, `kirigami.css`, `tables.css`,
  `calendar.css`, `application.css`). Put rules in those, not in `style.css`.
- Debug matching with `union-ruleinspector` (`--type`, `--id`, `--state`, `--hint`, `--verbose`)
  before opening an app — it prints the exact resolved property tree.

## Where this fits AgentOS
Union is the first credible **CSS-driven, scriptable** path for "the desktop restyles itself"
(the personalization/theming vision) — a deterministic, diff-able, version-controllable style
source the agent could propose edits to (model-proposes / code-disposes: edit CSS → ruleinspector
verifies → per-app preview → commit). But at 6.7 it's a preview behind Kvantum/WhiteSur in polish;
keep it a **watch-list capability**, not a substrate dependency. See `docs/adr/` if promoting it
to a real theming surface.
