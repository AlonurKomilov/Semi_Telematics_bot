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
    rollupOptions: {
      output: {
        // Split heavy vendor libs into their own chunks so:
        //  - editing a chart page doesn't invalidate the user's recharts cache
        //  - editing a map page doesn't invalidate the user's leaflet cache
        //  - the React/router/query bundle is reused across every navigation
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'query-vendor': ['@tanstack/react-query', '@tanstack/react-query-devtools', '@tanstack/react-table'],
          'chart-vendor': ['recharts'],
          'map-vendor': ['leaflet', 'leaflet.heat'],
          'ui-vendor': ['@base-ui/react', 'lucide-react', 'sonner'],
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
