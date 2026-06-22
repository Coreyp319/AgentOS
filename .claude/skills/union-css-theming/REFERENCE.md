# Union CSS theming — reference

Deep detail behind [SKILL.md](SKILL.md). Union 6.7.0 on KDE Plasma 6.7, CachyOS build
(`cachyos-extra-v3/union`). All paths/behaviour verified live 2026-06-22.

## 1. Architecture (three layers)
| Layer | Job | Artifact(s) |
|---|---|---|
| Input | Parse style descriptions into rules | `union-input-css.so` (CSS; Rust `cxx-rust-cssparser`). Pluggable — other input formats possible. |
| Intermediate | Abstract element/property/selector model + cascade | `libUnion.so`, headers in `/usr/include/union/Union/` (`Element`, `Selector`, `StyleRule`, `StyleLoader`, `PropertiesTypes`…) |
| Output | Render the model with a real toolkit | `UnionWidgetsStyle.so` (QtWidgets QStyle, registered as `Union`), `org.kde.union` QQC2 style (`/usr/lib/qt6/qml/org/kde/union/`), Kirigami platform plugin `kf6/kirigami/platform/org.kde.union.so` |
| Platform / color | Host integration + color source | `union-platform-plasma.so`, `union-colorprovider-kcolorscheme.so` |

One CSS source → both QtWidgets and QtQuick. That's the whole point: it replaces the situation
where Breeze (QStyle), Plasma SVG themes, and qqc2-desktop-style each styled a different slice.

## 2. Files on disk
- **System styles:** `/usr/share/union/css/`
  - `defaults/` — `default.css`, `extra-properties.css`, `generated-properties.css` (base property
    set every style builds on).
  - `styles/<name>/` — shipped: `breeze`, `breeze-mobile`, `breeze-rtl`. Each has `style.css`
    (entry, only `@import`s), `variables.css` (tokens), per-widget files, and `images/`.
- **User styles (override / custom):** `~/.local/share/union/css/` (any `$XDG_DATA_DIRS` entry works;
  Union uses QStandardPaths GenericDataLocation). Must contain BOTH:
  - `styles/<name>/` — your style.
  - `defaults/` — **a copy of the system defaults** (see gotcha #1). Without it Union throws on load.
- **Application Style descriptor:** `/usr/share/kstyle/themes/union.themerc` → `[KDE] WidgetStyle=Union`
  (this is the value the System Settings KCM writes to `kdeglobals`).
- **Tools:** `/usr/bin/union-ruleinspector`, `/usr/bin/union-colorinspector` (if present).
- **Logging categories:** `/usr/share/qlogging-categories6/union.categories`
  (enable with `QT_LOGGING_RULES="union.*=true"`).

## 3. Environment variables (from `libUnion.so`)
| Var | Effect |
|---|---|
| `UNION_STYLE_NAME` | Which named style under `…/union/css/styles/` to load. Default `breeze`. **Flatpak-safe** (ignored where Union isn't installed). |
| `UNION_STYLE_PLUGIN` | Which input plugin to use (default the CSS one). |
| `UNION_DISABLE_STYLE_CACHE` | `=1` bypass the compiled-style cache — use while iterating on CSS. |
| `UNION_FORCE_RECREATE_STYLE_CACHE` | `=1` rebuild the cache once (apply edits, keep caching). |
| `UNION_DISABLE_INPUT_PLUGINS` | Disable input plugins (debug). |
| `UNION_FORCE_PLATFORM` | Force a platform plugin (e.g. `plasma`). |

Toolkit selection (standard Qt, not Union-specific):
| Var | Effect |
|---|---|
| `QT_QUICK_CONTROLS_STYLE=org.kde.union` | Use Union's **native** QQC2 style for a QML app. **Do NOT set globally** — breaks Flatpak QML apps (style absent in sandbox). |
| `QT_STYLE_OVERRIDE=Union` | Use Union's QtWidgets QStyle for a single app without changing the global setting. |

## 4. Global activation vs per-app
- **Global (persistent):** `kdeglobals [KDE] widgetStyle=Union` — exactly what System Settings →
  Colors & Themes → Application Style → "Union (In Development)" writes. Sets the QtWidgets QStyle to
  Union; KDE QML controls drawn via `qqc2-desktop-style` then paint through that QStyle too. Applies
  to apps started **after** the change; relog for a clean full-session switch. The global session
  still uses the **default `breeze`** CSS unless you also set `UNION_STYLE_NAME` (env.d, Flatpak-safe).
- **Per-app (preview/iteration):** prefix the launch with the env vars (section 3). This is the
  recommended way to develop a style without disturbing the running desktop.
- **Revert global:** `kwriteconfig6 --file kdeglobals --group KDE --key widgetStyle kvantum`
  (restore the prior value — this box was `kvantum`/WhiteSur) + relog. Remove
  `~/.config/environment.d/union-style.conf` if you set a custom style name.

## 5. The CSS dialect
Standard CSS parsing (selectors, `@import`, `:root`, custom properties, comments) plus a KDE-aware
function set. Observed in `styles/breeze/`:
- **Tokens** live in `:root` in `variables.css` and per-file `:root` blocks: sizes
  (`--grid-unit: 18px`, `--corner-radius`, `--icon-size-*`, `--*-element-size`, `--scrollbar-size`),
  borders/shadows (`--button-border`, `--button-shadow`), and colors.
- **Functions:**
  - `custom-color("kcolorscheme", <set>, <role>, <ground>, <variant>)` — pull a live color from the
    user's KColorScheme. `<set>`: `active|inactive|disabled`. `<role>`: `window|button|view|selection|
    header|tooltip|complementary`. `<ground>`: `foreground|background|decoration`. `<variant>`:
    `normal|text|frame|focus|...`. This is why a Union style follows the user's color scheme instead
    of hard-coding hex.
  - `modify-color(<color> set-alpha <0..1>)` — adjust a channel (e.g. alpha).
  - `mix(<a>, <b>, <t>)` — linear blend, `t` in 0..1.
  - `var(--token)` — reference a custom property.
- **Selectors / matching:** by element **type** (`Button`, `CheckBox`, `Menu`, delegate types…),
  **id**, **state** (`hovered`, `pressed`, `checked`, `focused`, `disabled`…), and **hint**. The
  `style.css` entry must stay rules-free — only `@import`s; real rules go in the per-widget files.
- **Properties** map to the abstract model in `PropertiesTypes.h`: `layout` (alignment, width,
  height, spacing, padding, inset, margins), `text`, `background`, `border`, `shadow`, `icon`, etc.
  `union-ruleinspector` prints this resolved tree — read it to learn the property names.

## 6. union-ruleinspector
```
union-ruleinspector --style <style> [--type T] [--id ID] [--state S]... [--hint H]... [--verbose V]
```
- `--style` must come **first**. Use `org.kde.union` (the QQC2 style id).
- Honors `UNION_STYLE_NAME` to pick which CSS to inspect.
- Prints "Matched properties:" → the full resolved property tree for that element+state. Use it as
  the fast, GUI-free inner loop: edit CSS → ruleinspector → confirm the property changed.
- `--verbose 1` adds full query-matching logs (why a rule did/didn't match).

## 7. Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `rust cxxbridge … IO Error … defaults/default.css` | Custom style's data root lacks `defaults/`. `cp -r /usr/share/union/css/defaults ~/.local/share/union/css/defaults`. |
| CSS edits don't show | Style cache. Add `UNION_DISABLE_STYLE_CACHE=1` (iterate) or `UNION_FORCE_RECREATE_STYLE_CACHE=1` (apply once). |
| Flatpak QML apps break / blank | `QT_QUICK_CONTROLS_STYLE=org.kde.union` leaked globally. Unset it from env.d/profile; use per-app only. |
| Global widgetStyle=Union but QML apps still look Breeze-default | Session uses the default `breeze` CSS; set `UNION_STYLE_NAME` in `~/.config/environment.d/` and relog. |
| Want to confirm a rule matches without launching an app | `union-ruleinspector` (section 6). |
| Whole desktop looks like plain Breeze after activation | Expected — 6.7 Union ≈ Breeze and no custom CSS authored yet. Author `styles/agentos/` or revert to kvantum. |

## 8. Provenance / watch-list
- Union author: Arjen Hiemstra; QtWidgets style + themerc: Akseli Lahtinen. License: BSD-2-Clause
  (CSS) / MIT (themerc).
- Shipped Plasma 6.7.0 (2026-06-16) as a **tech preview of the QtQuick style**, off by default.
  Likely to become default in a later release if it matures.
- For AgentOS: a CSS source is deterministic, diff-able, and agent-editable — a natural fit for
  "model proposes a UI change, code disposes (ruleinspector validates + preview + commit)". Revisit
  for an ADR if/when Union's coverage and stability clear the bar to replace the SVG/Kvantum path.
