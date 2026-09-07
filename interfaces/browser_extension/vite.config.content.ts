import { defineConfig } from 'vite';

/**
 * The content script is built on its own because it is not a module.
 * Chrome loads `content_scripts` as CLASSIC scripts, so an `import`
 * statement in the output is a syntax error in the page — the overlay
 * simply never runs, with nothing in the extension's own console to
 * say why.  IIFE, one file, no chunks.
 *
 * `emptyOutDir: false` matters: this build runs after the main one and
 * would otherwise delete the panel and the worker it just made.
 */
export default defineConfig({
  // Without this the second build copies public/ again, leaving a stray
  // manifest.json and a duplicate icon set inside dist/content/ — files
  // Chrome would package and never read.
  publicDir: false,
  build: {
    outDir: 'dist/content',
    emptyOutDir: false,
    lib: {
      entry: 'src/content/mapsOverlay.ts',
      formats: ['iife'],
      name: 'FourTruckMapsOverlay',
      fileName: () => 'mapsOverlay.js',
    },
  },
});
