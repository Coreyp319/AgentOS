// ADR-0047 Phase 1 — unit test for the service-worker cache policy.
// Run:  node cache-policy.test.mjs   (prints 'N/N passed')
//
// The load-bearing assertion is the privacy one: NO /api/ path and NO dream-media
// path is ever runtime-cacheable, regardless of anything else (blocker #3).
import { readFileSync } from 'node:fs'

const code = readFileSync(new URL('./public/cache-policy.js', import.meta.url), 'utf8')
// The IIFE attaches to `self ?? globalThis`; in node module scope `self` is undefined,
// so indirect eval lands LucidCachePolicy on globalThis.
;(0, eval)(code)
const P = globalThis.LucidCachePolicy

let pass = 0, total = 0
const ok = (name, cond) => { total++; if (cond) pass++; else console.log('FAIL', name) }

// isApiPath
ok('api /api/state', P.isApiPath('/api/state') === true)
ok('api /api', P.isApiPath('/api') === true)
ok('api not /assets', P.isApiPath('/assets/x.js') === false)

// isDreamMedia
ok('media clip', P.isDreamMedia('/api/clip?id=1') === true)
ok('media frame', P.isDreamMedia('/api/frame?id=1') === true)
ok('media download', P.isDreamMedia('/api/download') === true)
ok('media share', P.isDreamMedia('/api/share') === true)
ok('media stash', P.isDreamMedia('/api/stash') === true)
ok('state is not media', P.isDreamMedia('/api/state') === false)

// isShellAsset
ok('shell hashed js', P.isShellAsset('/assets/index-abc123.js') === true)
ok('shell icon', P.isShellAsset('/icon-192.png') === true)
ok('shell manifest', P.isShellAsset('/manifest.webmanifest') === true)
ok('shell not api', P.isShellAsset('/api/state') === false)

// shouldRuntimeCache — the gate
ok('cache shell asset', P.shouldRuntimeCache('/assets/index-abc.js', true) === true)
ok('cache icon', P.shouldRuntimeCache('/icon-512.png', true) === true)
ok('no cache cross-origin', P.shouldRuntimeCache('/assets/index-abc.js', false) === false)
ok('no cache html (navigations handled separately)', P.shouldRuntimeCache('/index.html', true) === false)

// THE privacy invariant: every api / media path is NEVER cacheable.
const neverCache = [
  '/api/state', '/api/library', '/api/clip?id=1', '/api/frame?id=2', '/api/download',
  '/api/beats', '/api/openings', '/api/queue', '/api/share', '/api/stash', '/api/burn', '/api/delete',
]
for (const p of neverCache) {
  ok('NEVER cache ' + p, P.shouldRuntimeCache(p, true) === false)
}

console.log(`${pass}/${total} passed`)
if (pass !== total) process.exit(1)
