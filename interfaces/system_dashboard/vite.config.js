import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// System operator dashboard — deployed at system.4truck.us only.
// Kept INTENTIONALLY ISOLATED from the customer-facing dashboard so
// admin code never ships to customer browsers.  See
// docs/runbooks/system-dashboard-rollout.md for the deploy story.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(path.dirname(new URL(import.meta.url).pathname), './src'),
    },
  },
  base: '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
        },
      },
    },
  },
  server: {
    port: 5174,
    proxy: {
      // Local dev — proxy API calls to the FastAPI on 8080
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
});
