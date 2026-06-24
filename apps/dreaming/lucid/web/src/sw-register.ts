// ADR-0047 Phase 1 — register the service worker + the honest "box offline" banner.
//
// Kept dependency-free and outside the React tree so it adds nothing to the bundle
// graph and can't collide with the app's own state. Two jobs:
//   1. registerLucidSW() — install /sw.js (secure context only; never throws).
//   2. mountOfflineBanner() — a calm, accessible bar that tells the truth when the
//      box is unreachable, instead of leaving the user staring at a dead poll.
//
// "Box offline" is the case the interaction design cares about (the box asleep /
// VRAM-busy while the phone still has signal), which navigator.onLine alone cannot
// detect — so we also do a light, visible-only reachability ping to /api/state.
// This never generates anything locally; it only surfaces honest degraded state.

export function registerLucidSW(): void {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return
  // SWs require a secure context; localhost counts, tailscale serve provides HTTPS.
  if (!window.isSecureContext) return
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      /* registration is best-effort; the app works without it */
    })
  })
}

type Reach = 'online' | 'box-offline' | 'no-network'

function bannerText(state: Reach): string {
  if (state === 'no-network') return 'You’re offline — showing what’s saved on this device.'
  return 'Your box is offline — showing what’s saved. New clips resume when it’s back.'
}

export function mountOfflineBanner(opts: { pingMs?: number; timeoutMs?: number } = {}): () => void {
  if (typeof document === 'undefined') return () => {}
  const pingMs = opts.pingMs ?? 20_000
  const timeoutMs = opts.timeoutMs ?? 4_000

  const bar = document.createElement('div')
  bar.setAttribute('role', 'status')
  bar.setAttribute('aria-live', 'polite')
  bar.hidden = true
  // Calm, cool, non-alarming — a degraded state, not an error (honest-mapping).
  bar.style.cssText = [
    'position:fixed', 'top:0', 'left:0', 'right:0', 'z-index:9999',
    'padding:calc(8px + env(safe-area-inset-top)) 16px 8px',
    'font:500 13px/1.4 system-ui,sans-serif', 'text-align:center',
    'color:#c9cede', 'background:#1a1d28', 'border-bottom:1px solid #2a2e3c',
    'box-shadow:0 1px 0 rgba(0,0,0,.25)',
  ].join(';')
  document.body.appendChild(bar)

  let current: Reach = 'online'
  const paint = (state: Reach) => {
    current = state
    if (state === 'online') { bar.hidden = true; return }
    bar.textContent = bannerText(state)
    bar.hidden = false
  }

  async function probe(): Promise<void> {
    if (document.visibilityState !== 'visible') return
    if (typeof navigator !== 'undefined' && navigator.onLine === false) { paint('no-network'); return }
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), timeoutMs)
    try {
      const res = await fetch('/api/state', { cache: 'no-store', signal: ctrl.signal })
      paint(res.ok ? 'online' : 'box-offline')
    } catch (_) {
      paint('box-offline')
    } finally {
      clearTimeout(t)
    }
  }

  const onOffline = () => paint('no-network')
  const onOnline = () => { probe() }
  const onVisible = () => { if (document.visibilityState === 'visible') probe() }
  window.addEventListener('offline', onOffline)
  window.addEventListener('online', onOnline)
  document.addEventListener('visibilitychange', onVisible)
  const id = window.setInterval(probe, pingMs)
  probe()

  // teardown (unused in app, handy for tests / HMR)
  return () => {
    window.clearInterval(id)
    window.removeEventListener('offline', onOffline)
    window.removeEventListener('online', onOnline)
    document.removeEventListener('visibilitychange', onVisible)
    bar.remove()
    void current
  }
}

export function initLucidPwa(): void {
  registerLucidSW()
  mountOfflineBanner()
}
