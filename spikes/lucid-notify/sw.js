// ADR-0047 spike — Lucid service worker (push + notification-click only).
// Lucid has no service worker today; this is the minimal one the web-push leg
// needs. It is deliberately tiny: it does NOT cache dream content (blocker #3 —
// no private clip ever lands in Cache API), only handles the content-free push.
//
// The push payload is content-free by contract (see notify.py): title is the
// literal app name "Lucid", body is a fixed generic string, url carries only an
// opaque node id. The SW renders exactly what it is given and fetches nothing
// sensitive; the actual clip loads only after the user taps and the app
// foregrounds (and, for a private dream, after they unlock + unseal).

self.addEventListener('install', (e) => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let p = { body: 'Your dream grew — open Lucid to watch.', url: '/' };
  try { if (event.data) p = Object.assign(p, event.data.json()); } catch (_) {}
  // Hard stop: render only the generic app name client-side, never a dream title.
  event.waitUntil(self.registration.showNotification('Lucid', {
    body: p.body,
    tag: p.tag || 'lucid',          // coalesce a burst into one notification
    renotify: false,
    data: { url: p.url || '/' },
    badge: '/icon-192.png',
    icon: '/icon-192.png',
    // No image/thumbnail key — a frame must never render on the lock screen.
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil((async () => {
    const all = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of all) {
      if ('focus' in c) { c.navigate(url); return c.focus(); }
    }
    if (clients.openWindow) return clients.openWindow(url);
  })());
});
