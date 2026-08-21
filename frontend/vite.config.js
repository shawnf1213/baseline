import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: [
        'favicon.svg',
        'apple-touch-icon.png',
        'baseline-logo.png',
      ],
      manifest: {
        name: 'Baseline',
        short_name: 'Baseline',
        description: 'Tennis prop research — projections, player form and surface splits.',
        theme_color: '#0a0a0a',
        background_color: '#0a0a0a',
        display: 'standalone',
        orientation: 'portrait',
        start_url: '/',
        scope: '/',
        icons: [
          { src: '/pwa-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          { src: '/pwa-maskable-192.png', sizes: '192x192', type: 'image/png', purpose: 'maskable' },
          { src: '/pwa-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // ── SHIP UPDATES WITHOUT WAITING FOR EVERY WINDOW TO CLOSE ────────────
        // registerType 'autoUpdate' fetches a new service worker, but by default
        // it sits in "waiting" until every tab and installed window of the app
        // is gone. Anyone with the app open in a background tab, or a
        // full-screen window they never close, keeps being served the OLD
        // precached bundle — which is exactly how one window showed the new
        // build and another showed a months-old one.
        //
        // skipWaiting activates the new worker as soon as it installs;
        // clientsClaim puts existing pages under it without a second reload.
        // Together they mean a deploy reaches people on their next load rather
        // than whenever they happen to close every window.
        //
        // The tradeoff is that assets can swap under a long-lived session. It is
        // acceptable here: the app is a read-only research surface with no
        // half-finished forms to lose, and being stuck on a stale build is the
        // far worse failure.
        skipWaiting: true,
        clientsClaim: true,
        // Do not leave old precaches on disk after an update.
        cleanupOutdatedCaches: true,
        // Precache the built app shell (JS/CSS/HTML/fonts/images).
        globPatterns: ['**/*.{js,css,html,svg,png,woff,woff2}'],
        // SPA: unknown routes fall back to the app shell (works offline).
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api/],
        runtimeCaching: [
          {
            // Player headshots. MUST sit ahead of the generic /api rule below,
            // because runtimeCaching is first-match-wins and NetworkFirst with a
            // 6-hour expiry would re-fetch a photograph that never changes.
            // Sourced from our own endpoint now (Sofascore 403s its image route
            // from everywhere), which redirects to Wikimedia.
            urlPattern: ({ url }) => url.pathname.startsWith('/api/player/image')
              || /(^|\.)(wikimedia|wikipedia)\.org$/.test(url.hostname),
            handler: 'CacheFirst',
            options: {
              cacheName: 'baseline-player-photos',
              expiration: { maxEntries: 300, maxAgeSeconds: 60 * 60 * 24 * 30 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            // All backend API calls — always try the network first so research
            // data is fresh; fall back to the last cached response when offline.
            urlPattern: ({ url }) => url.pathname.startsWith('/api'),
            handler: 'NetworkFirst',
            options: {
              cacheName: 'baseline-api',
              networkTimeoutSeconds: 10,
              expiration: { maxEntries: 200, maxAgeSeconds: 60 * 60 * 6 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            // Google Fonts stylesheet + files.
            urlPattern: ({ url }) => /(^|\.)(googleapis|gstatic)\.com$/.test(url.hostname),
            handler: 'CacheFirst',
            options: {
              cacheName: 'baseline-fonts',
              expiration: { maxEntries: 20, maxAgeSeconds: 60 * 60 * 24 * 365 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
    }),
  ],
  // Dev-server proxies mirror the vercel.json rewrites so local dev talks to the
  // same live sources without cross-origin CORS: /pp → PrizePicks (browsers can't
  // hit it directly), /api → the backend.
  server: {
    port: 5173,
    proxy: {
      '/pp': {
        target: 'https://partner-api.prizepicks.com',
        changeOrigin: true,
        secure: true,
        rewrite: (p) => p.replace(/^\/pp/, ''),
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.207 Safari/537.36',
          'Accept': 'application/json',
        },
      },
      // Underdog's board API is unauthenticated but browser-CORS-locked, same as
      // PrizePicks — so it goes through a same-origin proxy rather than direct.
      '/ud': {
        target: 'https://api.underdogfantasy.com',
        changeOrigin: true,
        secure: true,
        rewrite: (p) => p.replace(/^\/ud/, ''),
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
          'Accept': 'application/json',
        },
      },
      '/api': {
        target: 'https://backend-production-84ab.up.railway.app',
        changeOrigin: true,
        secure: true,
      },
    },
  },
})
