import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MotionConfig } from 'motion/react'
import '@fontsource-variable/fraunces' // self-hosted, bundled into dist — NO CDN (per the type review)
import './theme.css'
import App from './App'

// gcTime 10min: the beats query is unobserved for the whole (multi-minute) 'dreaming' phase, so a 60s
// gcTime evicted it mid-turn and the held menu was lost on return. The server now guarantees the hold,
// but a longer window also avoids a needless cold refetch when the user comes back to the same frame.
const qc = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, gcTime: 600_000 } },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      {/* motion/react does NOT honour prefers-reduced-motion unless told to; the CSS media query can't
          reach JS-driven transitions. reducedMotion="user" collapses every <motion.*> to instant under
          the OS preference — the script-layer counterpart to theme.css's reduced-motion fork. */}
      <MotionConfig reducedMotion="user">
        <App />
      </MotionConfig>
    </QueryClientProvider>
  </React.StrictMode>,
)
