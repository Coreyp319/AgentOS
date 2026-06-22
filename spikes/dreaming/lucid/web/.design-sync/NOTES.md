# design-sync notes — AgentOS Lucid

This package (`spikes/dreaming/lucid/web`) is an **application, not a packaged component
library** — there is no library build, no shipped `.d.ts`. The sync runs the package shape
in a deliberately-shaped way; the bullets below are why.

## Setup gotchas (read before a re-sync)

- **Explicit barrel entry, NOT src synthesis.** `cfg.entry = ".design-sync/ds-entry.ts"`
  re-exports only the 9 shippable components. Without it, the converter synthesizes an
  entry from *every* `src/*.tsx`, which pulls in `main.tsx`'s top-level
  `ReactDOM.createRoot(...).render(<App/>)` — that throws at bundle-eval (no `#root`) and
  the IIFE aborts before assigning `window.AgentOSLucid`, so the export smoke check fails
  `[BUNDLE_EXPORT] 9/9 not a component`. It also drags in the page views (App/Start/…).
  **Adding/removing a DS component means editing `ds-entry.ts` AND `componentSrcMap`
  (pin) AND `dtsPropsFor` (props).**
- **Hand-written prop contracts.** No build ⇒ no shipped `.d.ts` ⇒ ts-morph can't extract
  the inline prop types, so every component would emit `{ [key: string]: unknown }`. The
  real contracts live in `cfg.dtsPropsFor` (inlining the `Engine`/`Readiness`/`StashStatus`
  shapes from `src/api.ts`). If a component's props change in source, update `dtsPropsFor`
  by hand — nothing auto-syncs it.
- **`componentSrcMap` pins the 9 to their shared files** (`src/components.tsx`,
  `src/Library.tsx`) so src-enrichment (group/JSDoc) finds them — the default fuzzy-find
  looks for `<Name>.tsx`/`<Name>/index.tsx`, which don't exist (many components per file).
  The 5 page views (App/Start/Dreaming/Chain/QueuePanel) are excluded via `: null`.
- **Preview environment (`cfg.extraEntries` → `.design-sync/lucid-preview-env.tsx`).** It
  is bundled INTO `_ds_bundle.js` on purpose: it provides (1) the dark "instrument" stage
  — components use near-white text on the app's dark `body` gradient and are invisible on a
  white card — and (2) a `@tanstack/react-query` `QueryClient`, seeded with sample
  `['library']`/`['stash']` data so `DreamGallery`/`StashPanel` render populated.
  **The alternate-state wrappers (`StashCreateEnv`/`StashLockedEnv`/`LibraryEmptyEnv`)
  MUST stay in this bundled module.** A `QueryClientProvider` imported directly into a
  preview `.tsx` is a *different* react-query instance and its context won't reach the
  component (it would render empty/loading). Nest the bundled envs instead.
- **Disclosure previews open the `<details>` at mount.** `EngineToggle`/`PreviewToggle`
  are collapsed `<details>`; their previews wrap the component in a small `Open` helper
  (a ref + `useEffect` that sets the `open` attribute) so the card shows the real picker,
  not just the one-line summary bar. The open state can't be set via props.
- **DreamGallery thumbnails 404 with no backend** → the tile's designed blank-thumb
  fallback (a subtle gradient). Expected, not a defect. Sealed StashPanel tiles never have
  thumbnails by design.
- **The `node_modules/web` self-symlink is NOT needed** (it was a synth-mode artifact).
  With `cfg.entry` set, `PKG_DIR` resolves by walking up from the barrel. Build + validate
  verified clean with no symlink — a fresh clone needs nothing extra here.

## Render check / playwright

- Browsers are cached at `~/.cache/ms-playwright`. **No published playwright version pins
  chromium build 1226** (1.60.0→1223, 1.61.0→1228), and 1226 is what's cached. So the
  render check drives the cached binary directly:
  `DS_CHROMIUM_PATH="$HOME/.cache/ms-playwright/chromium-1226/chrome-linux64/chrome"` with
  `playwright@1.61.0` installed in `.ds-sync`. **Set `DS_CHROMIUM_PATH` on every
  `package-validate.mjs` / `package-capture.mjs` / `resync.mjs` run** or it fails
  `[RENDER_SKIPPED]` / "Executable doesn't exist".

## Known render warns
- None. (9/9 render cleanly, 0 bad/thin/variantsIdentical.)

## Re-sync risks (what can silently go stale)
- **Three places to update per component change**: `ds-entry.ts` (re-export),
  `componentSrcMap` (pin/exclude), `dtsPropsFor` (props). Miss one and the component is
  dropped, mis-grouped, or ships a lossy `{ [key: string]: unknown }` contract.
- **`dtsPropsFor` is a hand-written mirror of the source prop types** — it does not track
  `src/components.tsx` / `src/Library.tsx`. Re-diff it against source on a re-sync if those
  files changed.
- **Seeded preview data is inlined** in `lucid-preview-env.tsx` (mirrors `LibraryDream` /
  `StashDream` in `src/api.ts`). Preview-only — it can't rot the real components — but if
  those types change, refresh the seeds so the gallery/stash previews stay realistic.
- **App-coupled by nature.** These are Lucid-specific surfaces that assume the dark
  instrument stage and a QueryClient; they are not a generic reusable kit. This is the
  current `src/` snapshot of a spike app — if components move/rename, the maps above need
  updating before re-sync.
- **All 9 land in group `general`.** Grouping was deferred (no docs tree / `@category`).
  A future pass could split them (Navigation / Status / Controls / Dream management /
  Library) via `@category` JSDoc or `docsMap` category stubs.
