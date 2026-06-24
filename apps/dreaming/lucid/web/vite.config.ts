import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base './' so the Python server can serve the built bundle from / with relative asset paths.
// Everything self-hosted (fonts, Vidstack, JS) — no CDN, works offline/loopback. Dev proxies the
// API + media to the running lucid_web.py so `npm run dev` talks to the real backend.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 1800 },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/healthz': 'http://127.0.0.1:8765',
    },
  },
})
