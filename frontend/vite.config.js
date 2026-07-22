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
        // Precache the built app shell (JS/CSS/HTML/fonts/images).
        globPatterns: ['**/*.{js,css,html,svg,png,woff,woff2}'],
        // SPA: unknown routes fall back to the app shell (works offline).
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api/],
        runtimeCaching: [
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
            // Player headshots pulled straight from Sofascore by id — rarely
            // change, so cache-first for instant loads.
            urlPattern: ({ url }) => /(^|\.)sofascore\.(com|app)$/.test(url.hostname),
            handler: 'CacheFirst',
            options: {
              cacheName: 'baseline-player-photos',
              expiration: { maxEntries: 300, maxAgeSeconds: 60 * 60 * 24 * 30 },
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
  server: { port: 5173 },
})
