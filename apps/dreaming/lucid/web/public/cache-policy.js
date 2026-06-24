// ADR-0047 Phase 1 — Lucid service-worker cache policy (pure, testable).
//
// This is the privacy gate for the offline PWA shell (responsible-ai-privacy-
// skeptic, blocker #3): the service worker may cache the *app shell* (the static
// build assets that boot the UI), but must NEVER cache a dream clip, a frame, a
// download, the share inbox, the stash, or ANY /api/ response — those would land
// a private/mature artifact (or its metadata) in the phone's Cache API, where it
// survives in device backups. The single gate `shouldRuntimeCache` fails closed.
//
// Loaded both by sw.js (via importScripts → `self`) and by the node test (via
// `globalThis`), so the rules live in exactly one place and are unit-tested.

(function (root) {
  function isApiPath(p) {
    return p === '/api' || p.startsWith('/api/');
  }

  // Dream content / private surfaces. Never cached, never even revalidated.
  function isDreamMedia(p) {
    return /^\/api\/(clip|frame|download|share|stash|burn|delete)\b/.test(p);
  }

  // The static app shell: Vite's hashed build output + the icon/manifest family.
  // HTML navigations are handled separately (network-first → cached '/').
  function isShellAsset(p) {
    return p.startsWith('/assets/') ||
      /\.(js|mjs|css|woff2?|ttf|otf|png|svg|ico|webmanifest)$/.test(p);
  }

  // THE gate. Cache a GET only if it is same-origin, NOT an API call (so no dream
  // content or metadata is ever stored), and a static shell asset.
  function shouldRuntimeCache(path, sameOrigin) {
    if (!sameOrigin) return false;
    if (isApiPath(path)) return false;   // blocker #3: no /api/ response ever cached
    return isShellAsset(path);
  }

  root.LucidCachePolicy = { isApiPath, isDreamMedia, isShellAsset, shouldRuntimeCache };
})(typeof self !== 'undefined' ? self : (typeof globalThis !== 'undefined' ? globalThis : this));
