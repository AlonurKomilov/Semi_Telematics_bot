import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

// vite-plugin-pwa.
// The miniapp is now a Telegram WebApp PWA: an install-able shell with
// a runtime-cached copy of the scorecard so it stays useful when
// a driver opens it on flaky cellular signal.
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.ico'],
      manifest: {
        name: '4truck Driver',
        short_name: '4truck',
        description: 'Scorecard and fleet status',
        theme_color: '#0a84ff',
        background_color: '#000000',
        display: 'standalone',
        scope: '/',
        start_url: '/',
        icons: [],
      },
      workbox: {
        navigateFallback: '/index.html',
        // Don't precache map tiles or large images — they're served by
        // third parties and would blow the cache budget.
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        runtimeCaching: [
          {
            // Scorecard — short SWR window so a fresh open shows
            // recent data but offline still works.
            urlPattern: ({ url }) => url.pathname.startsWith('/api/safety/scorecards/me'),
            handler: 'StaleWhileRevalidate',
            options: {
              cacheName: 'scorecard',
              expiration: { maxEntries: 8, maxAgeSeconds: 60 * 60 * 24 },
            },
          },
          {
            // Fleet vehicle list — backs the OfflineBanner with last-known
            // truck positions when the network drops.
            urlPattern: ({ url, request }) =>
              url.pathname.startsWith('/api/vehicles/') && request.method === 'GET',
            handler: 'NetworkFirst',
            options: {
              cacheName: 'st-fleet',
              networkTimeoutSeconds: 4,
              expiration: { maxEntries: 8, maxAgeSeconds: 5 * 60 },
            },
          },
          {
            // Pending-alert badge count — keep the last value to avoid
            // a flash of "0" when the request times out offline.
            urlPattern: ({ url, request }) =>
              url.pathname === '/api/alerts/pending/count' && request.method === 'GET',
            handler: 'NetworkFirst',
            options: {
              cacheName: 'st-alerts-count',
              networkTimeoutSeconds: 2,
              expiration: { maxEntries: 4, maxAgeSeconds: 60 },
            },
          },
          {
            // Generic API GETs — network first with a 24h fallback.
            urlPattern: ({ url, request }) =>
              url.pathname.startsWith('/api/') && request.method === 'GET',
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-get',
              networkTimeoutSeconds: 4,
              expiration: { maxEntries: 64, maxAgeSeconds: 60 * 60 * 24 },
            },
          },
        ],
      },
    }),
  ],
  // base now serves at site root for the dedicated app.4truck.us
  // subdomain.  Legacy apex paths (4truck.us/miniapp/*) are 301-redirected
  // by nginx so existing Telegram WebView shortcuts still resolve until
  // the bot's WEBAPP_URL env var has been updated to https://app.4truck.us.
  base: process.env.VITE_BASE_PATH ?? '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // Split heavy third-party deps into their own chunks so they
        // (a) cache independently across deploys and (b) load on demand
        // when the lazy page that needs them is first opened.
        manualChunks: {
          'leaflet':       ['leaflet', 'leaflet.markercluster'],
          'tg-ui':         ['@telegram-apps/telegram-ui', '@vkontakte/icons'],
          'react-vendor':  ['react', 'react-dom'],
        },
      },
    },
  },
  server: {
    port: 8003,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
});
