// Preview environment for the AgentOS Lucid design system (design-sync cfg.provider).
//
// Why this exists: several Lucid components read from a @tanstack/react-query
// QueryClient — EngineToggle/PrivateCard/LibraryCard call mutation hooks (which
// throw "No QueryClient set" if mounted bare), and StashPanel/DreamGallery fetch
// their own data via useStash()/useLibrary(). To render real, populated preview
// cards statically (no live Lucid backend), this module:
//   1. provides a QueryClient with network-y behaviour disabled,
//   2. pre-seeds the ['library'] and ['stash'] caches with realistic data, and
//   3. paints the app's dark "instrument" body surface behind every preview
//      (theme.css styles `body`, which a preview card doesn't inherit; the
//      components use near-white text and would be invisible on a white card).
//
// IMPORTANT: these wrappers are bundled INTO _ds_bundle.js (cfg.extraEntries), so
// their QueryClientProvider uses the SAME react-query instance as the components.
// A QueryClientProvider imported directly into a preview .tsx would be a second
// instance and its context would NOT reach the component. That's why the
// alternate-state envs (empty/locked/create) are exported from HERE and used by
// wrapping a component cell in them — never re-seeded from the preview itself.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

// Date.now() is real in the browser at card-render time, so relative timestamps
// render as calm "10 min ago" / "2 d ago" rather than a frozen absolute date.
const now = Date.now() / 1000
const mins = (m: number) => now - m * 60
const hours = (h: number) => now - h * 3600
const days = (d: number) => now - d * 86400

// Realistic saved (non-private) dreams for DreamGallery. Thumbnails resolve to
// /api/library/thumb (404 with no backend) → the tile's designed blank-thumb
// fallback, exactly as a freshly-saved dream looks before its still is rendered.
const LIBRARY = [
  { session: 'd-aurora', name: 'Aurora over still water', frames: 7, updated: mins(8), created: days(3) },
  { session: 'd-orchard', name: 'A door in the orchard', frames: 5, updated: hours(5), created: days(2) },
  { session: 'd-tideline', name: 'Walking the tide line at dusk', frames: 9, updated: days(1), created: days(4) },
  { session: 'd-library', name: 'The library that kept growing', frames: 4, updated: days(2), created: days(6) },
]

// Realistic sealed dreams for StashPanel's unlocked grid. Sealed tiles never show
// a thumbnail by design (just the 🔒 seal-mark), so this grid renders perfectly
// with no images at all.
const STASH_DREAMS = [
  { id: 's-letter', name: 'Letter I never sent', frames: 6, updated: hours(2), created: days(5) },
  { id: 's-quiet', name: 'The quiet room', frames: 3, updated: days(1), created: days(7) },
  { id: 's-hands', name: 'Hands in the dark', frames: 8, updated: days(3), created: days(9) },
]

type Seed = { library?: unknown; stash?: unknown }

function makeClient({ library = LIBRARY, stash = { exists: true, unlocked: true, dreams: STASH_DREAMS } }: Seed = {}) {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnMount: false, refetchOnWindowFocus: false, refetchInterval: false, staleTime: Infinity },
    },
  })
  qc.setQueryData(['library'], library)
  qc.setQueryData(['stash'], stash)
  return qc
}

// The dark instrument stage — the app's body surface, reproduced so previews sit
// on the correct dark backdrop instead of a bare white card.
const stage = {
  color: 'var(--inst-text)',
  font: '15px/1.55 system-ui, sans-serif',
  background:
    'radial-gradient(1000px 600px at 80% -10%, color-mix(in srgb, var(--inst-blue) 8%, transparent), transparent 60%),' +
    ' linear-gradient(160deg, var(--inst-base), var(--inst-deep) 45%, var(--inst-horizon))',
  padding: '28px 24px',
  minHeight: '100%',
  boxSizing: 'border-box' as const,
}

// cfg.provider — wraps every preview: dark stage + the default (fully-populated) client.
const defaultClient = makeClient()
export function LucidPreviewEnv({ children }: { children?: ReactNode }) {
  return (
    <QueryClientProvider client={defaultClient}>
      <div style={stage}>{children}</div>
    </QueryClientProvider>
  )
}

// Alternate-state envs — nest INSIDE LucidPreviewEnv (so they inherit the dark
// stage) and override just the react-query cache for their subtree. Use them to
// show StashPanel / DreamGallery states the default seed doesn't cover.
const emptyLibraryClient = makeClient({ library: [] })
export function LibraryEmptyEnv({ children }: { children?: ReactNode }) {
  return <QueryClientProvider client={emptyLibraryClient}>{children}</QueryClientProvider>
}

const stashCreateClient = makeClient({ stash: { exists: false, unlocked: false } })
export function StashCreateEnv({ children }: { children?: ReactNode }) {
  return <QueryClientProvider client={stashCreateClient}>{children}</QueryClientProvider>
}

const stashLockedClient = makeClient({ stash: { exists: true, unlocked: false } })
export function StashLockedEnv({ children }: { children?: ReactNode }) {
  return <QueryClientProvider client={stashLockedClient}>{children}</QueryClientProvider>
}
