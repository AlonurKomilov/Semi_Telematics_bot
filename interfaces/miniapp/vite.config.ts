import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/miniapp/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 8003,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
});
