import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

/**
 * Test runner config — kept minimal so the smoke harness loads
 * quickly and stays in sync with the Vite build (same plugin set,
 * same JSX runtime).  jsdom is the env so hook tests using
 * @testing-library/react have a DOM to render into; pure-module
 * tests (e.g. the overlay registry) run fine in the same env.
 */
export default defineConfig({
  plugins: [react()],
  // Mirror the Vite build's "@/" alias so tests can import components
  // that use it (e.g. ui/tooltip -> "@/lib/utils").
  resolve: {
    alias: { '@': new URL('./src', import.meta.url).pathname },
  },
  test: {
    environment: 'jsdom',
    globals: false,
    // Unmounts rendered components between tests.  REQUIRED here rather
    // than optional: Testing Library's own auto-cleanup installs on the
    // global afterEach, which ``globals: false`` removes — so without
    // this, renders accumulate and a test can pass by finding the
    // PREVIOUS test's DOM.  See src/test/setup.ts.
    setupFiles: ['./src/test/setup.ts'],
    // src/**/*.test.{ts,tsx} — application code tests.
    // scripts/**/*.test.mjs  — integration tests for the audit
    // scripts (check-role-drift, check-layout-coverage); they
    // spawn the script as a child process so they need .mjs.
    include: [
      'src/**/*.test.{ts,tsx}',
      'scripts/**/*.test.{mjs,ts}',
    ],
    css: false,
  },
});
