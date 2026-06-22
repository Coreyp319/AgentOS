import { Nav } from 'web'

const noop = () => {}

// The section nav with no live dream — the resting state on first load.
export const NewDream = () => <Nav active="new" hasDream={false} onNavigate={noop} />

// With a live dream: it leads the row as its own named item with an "up" dot.
export const WithLiveDream = () => (
  <Nav active="dream" hasDream dreamName="Aurora over still water" onNavigate={noop} />
)

// Browsing the saved library while a dream is live (the live item stays first).
export const OnLibrary = () => (
  <Nav active="library" hasDream dreamName="A door in the orchard" onNavigate={noop} />
)
