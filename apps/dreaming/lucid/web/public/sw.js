// ADR-0047 Phase 1 — Lucid service worker (the PWA-completion foundation).
//
// Lucid had no service worker. This one does the minimum to make the installed
// PWA boot offline and feel instant, WITHOUT ever caching dream content:
//   - precache the app shell on install (index.html + icon/manifest family);
//   - navigations: network-first, fall back to cached '/' so the app boots when
//     the box is unreachable (it then shows the honest "box offline" state and
//     the library of what's saved — never a fake local generator);
//   - static build assets: stale-while-revalidate (instant load, self-updating);
//   - EVERYTHING under /api/ (clips, frames, downloads, state, library, stash):
//     network-only, NEVER cached. The cache gate lives in cache-policy.js.
//
// Privacy invariant (blocker #3): no cache.put() ever runs for an /api/ URL or a
// dream-media URL. The only writes are shell assets vetted by shouldRuntimeCache.

importScripts('/cache-policy.js');
const P = self.LucidCachePolicy;

const CACHE = 'lucid-shell-v1';
const SHELL = [
  '/', '/manifest.webmanifest', '/favicon.svg', '/apple-touch-icon.png',
  '/icon-192.png', '/icon-512.png',
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL).catch(() => {})) // tolerate a missing optional asset
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;               // never touch writes
  const url = new URL(req.url);
  const sameOrigin = url.origin === self.location.origin;

  // Navigations: network-first, fall back to the cached shell so the app boots offline.
  if (req.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        return await fetch(req);
      } catch (_) {
        return (await caches.match('/')) || Response.error();
      }
    })());
    return;
  }

  // Anything under /api/ (and all dream media): network-only, never cached.
  if (!sameOrigin || P.isApiPath(url.pathname)) return;

  // Static shell assets: stale-while-revalidate, gated by the policy.
  if (P.shouldRuntimeCache(url.pathname, sameOrigin)) {
    event.respondWith((async () => {
      const cached = await caches.match(req);
      const network = fetch(req).then((res) => {
        if (res && res.ok && res.type === 'basic') {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));   // only ever a vetted shell asset
        }
        return res;
      }).catch(() => cached);
      return cached || network;
    })());
  }
});
