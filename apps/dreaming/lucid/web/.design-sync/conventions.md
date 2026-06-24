# AgentOS Lucid — design system conventions

AgentOS Lucid is the **dark "instrument" UI** for a local dream-video viewer: calm,
editorial, translucent glass on a deep-blue gradient. Every component is real compiled
React on `window.AgentOSLucid.*`. Build screens by composing these components and writing
your own layout glue with the tokens and classes below — never a parallel palette.

## Setup — wrap every screen in two things

1. **The dark instrument surface.** Components use near-white text and semi-transparent
   "glass" panels designed against the app's dark gradient `body`. On a white background
   the text is invisible. Put your screen on that surface:
   ```css
   body { color: var(--inst-text);
     background: linear-gradient(160deg, var(--inst-base), var(--inst-deep) 45%, var(--inst-horizon)); }
   ```
2. **A QueryClient.** `EngineToggle`, `PrivateCard`, `LibraryCard`, `StashPanel`, and
   `DreamGallery` read state and call mutation hooks via `@tanstack/react-query` — mount
   them under a `<QueryClientProvider>` or they throw "No QueryClient set". For a static
   mockup with no backend, `window.AgentOSLucid.LucidPreviewEnv` wraps a ready QueryClient
   (seeded with sample library/stash data) **and** the dark stage — wrap your tree in it.

## Styling idiom — semantic classes + CSS-variable tokens

There are **no utility classes and no style props**. The look comes from (a) the semantic
class names the components already carry, and (b) `--*` custom properties on `:root`
(defined in `styles.css`). Style your own glue with the SAME tokens and classes.

**Tokens** — `var(--…)`:

| group | tokens |
|---|---|
| surface bg | `--inst-base` `--inst-deep` `--inst-horizon` |
| text | `--inst-text` `--inst-muted` `--inst-label` |
| accent | `--inst-blue` `--inst-warm` `--brand-warm` |
| glass / edges | `--glass` `--hairline` `--blur-raised` |
| status | `--st-up` `--st-idle` `--st-red` `--st-amber` `--st-unknown` |
| type / shape | `--display` (Fraunces serif) `--fs-sm` `--fs-xs` `--radius-sm` `--radius-md` |

**Classes** to reuse for your own layout:

| role | class |
|---|---|
| glass panel | `.card` ( `.card.private` for the private tint ) |
| primary button | `.beat` ( `.beat.warm` = warm CTA, `.beat.danger` = destructive ) |
| secondary button | `.ghost` |
| inline status line | `.banner` ( `.banner.good` / `.banner.bad` ) |
| muted caption | `.note` |
| horizontal control group | `.row` |
| page column | `.wrap` ( `.wrap.wide` for the roomy dream view ) |
| status dot | `.dot` ( + `.on` / `.off` / `.ok` / `.paused` ) |
| collapsed disclosure | `.disc` (an expert control that recedes — see `EngineToggle`) |

**Fraunces is for authored narrative only** (`--display`, `.mark`, `.tag`): the wordmark,
captions, the elapsed timer, model-written beat/gutter lines. All data and UI text stays
`system-ui`.

## Where the truth lives
- `styles.css` (and the component CSS it `@import`s) is the full token + class source —
  read it before styling.
- Each component's `*.prompt.md` (usage) and `*.d.ts` (props) under `components/general/<Name>/`.

## One idiomatic composition
```tsx
const L = window.AgentOSLucid
// Static mockup: LucidPreviewEnv supplies the dark stage + a seeded QueryClient.
<L.LucidPreviewEnv>
  <div className="wrap">
    <L.ReadinessCard r={{ coordinator: true, comfyui: true, ollama: true, can_dream: true, why: [] }} />
    <L.EngineToggle engine={{ active: 'wan', options: ['wan', '10eros'] }} />
    <div className="card">
      <p className="note">Your own layout glue — built from the same tokens.</p>
      <div className="row">
        <button className="beat">Dream it</button>
        <button className="ghost">Cancel</button>
      </div>
    </div>
  </div>
</L.LucidPreviewEnv>
```
