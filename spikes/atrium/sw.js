/* The Atrium service worker — the minimum that makes the launch view installable and
 * honestly-offline (ADR-0031 gap #5). It caches only the STATIC SHELL (HTML + icons +
 * manifest). It deliberately does NOT cache /launch.json: live service state must never be
 * served stale-as-fresh. When the box is unreachable the cached shell loads, the page's own
 * /launch.json fetch fails, and the page shows the honest "tailnet path is quiet" blind state.
 *
 * Network-first for the navigation shell (so an updated panel reaches the phone), cache
 * fallback when offline; cache-first for the immutable icons/manifest.
 */
const CACHE = "atrium-shell-v1";
const SHELL = ["/", "/manifest.webmanifest",
               "/icons/icon-192.png", "/icons/icon-512.png", "/icons/icon-512-maskable.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // Never intercept live-state endpoints — let them hit the network and fail honestly offline.
  if (url.pathname === "/launch.json" || url.pathname === "/status.json") return;

  // Navigation shell: network-first, fall back to the cached page when offline.
  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req).then((res) => {
        // Only cache a GOOD page — never poison the shell with a 4xx/5xx error page.
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put("/", copy)).catch(() => {});
        }
        return res;
      }).catch(() => caches.match("/").then((r) => r || caches.match(req)))
    );
    return;
  }

  // Static assets (icons, manifest): cache-first.
  if (SHELL.includes(url.pathname)) {
    e.respondWith(caches.match(req).then((r) => r || fetch(req)));
  }
});
