import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Two entries: the side panel page and the service worker.  Everything
// is bundled — Manifest V3 forbids remote code, so Leaflet ships inside
// the package instead of arriving from a CDN the way the dashboard's
// useLeafletMap loads it.  manifest.json lives in public/ so Vite copies
// it to the dist root, where Chrome expects it.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        sidepanel: 'sidepanel.html',
        background: 'src/background.ts',
      },
      output: {
        entryFileNames: '[name].js',
        chunkFileNames: 'chunks/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
});
