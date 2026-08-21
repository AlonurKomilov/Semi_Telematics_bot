import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(path.dirname(new URL(import.meta.url).pathname), './src'),
    },
  },
  // base + router are now served at site root for the dedicated
  // dash.4truck.us subdomain.  Legacy apex paths (4truck.us/dashboard/*)
  // are 301-redirected by nginx so existing bookmarks still resolve.
  // Override via VITE_BASE_PATH for alternate deployments.
  base: process.env.VITE_BASE_PATH ?? '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Pages are already route-lazy (see router.tsx), so the ``index``
    // chunk is just the always-loaded shared core (shell, contexts,
    // i18n, design-system primitives).  ~150 kB gzip is fine for that;
    // raise the warn threshold so a healthy shared core doesn't nag.
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        // Split heavy vendor libs into their own chunks so:
        //  - editing a map page doesn't invalidate the user's leaflet cache
        //  - the React/router/query bundle is reused across every navigation
        //  - i18n + small utils ride their own long-lived chunks instead
        //    of bloating the shared ``index`` core
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'query-vendor': ['@tanstack/react-query', '@tanstack/react-query-devtools', '@tanstack/react-table'],
          // recharts and leaflet are only reached via lazy pages —
          // Rollup bundles each into a shared async chunk for those
          // pages automatically (same cache stability, correct hash).
          // FORCING them into manualChunks backfires two ways: leaflet
          // produced an empty chunk, and recharts — which shares
          // modules (react-is) with the eager util-vendor — got
          // chunk-order-linked into the ENTRY as a bare side-effect
          // import: 115 kB gzip of chart code preloaded on every
          // page, measured by the 2026-08-21 perf audit on a page
          // with zero charts.  Lazy-only libs stay OUT of this map.
          // three IS also lazy-only, but unlike leaflet the split works
          // (verified in dist: a real chunk, imported only by Scene) —
          // it exists for CACHE STABILITY: editing our Scene code must
          // not re-download ~260 kB gzip of unchanged 3D libraries.
          'three-vendor': ['three'],
          'ui-vendor': ['@base-ui/react', 'lucide-react', 'sonner'],
          'i18n-vendor': ['i18next', 'react-i18next', 'i18next-browser-languagedetector'],
          'util-vendor': ['clsx', 'tailwind-merge', 'class-variance-authority', 'react-is'],
        },
      },
    },
  },
  server: {
    port: 8002,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
});
