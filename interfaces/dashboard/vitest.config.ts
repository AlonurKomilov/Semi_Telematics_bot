import { cpus } from 'node:os';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

/**
 * Worker cap — leave the machine some headroom.
 *
 * Vitest defaults to one worker per core.  This repo is developed on a
 * box that routinely runs several coding agents plus their indexers, so
 * "one worker per core" means the suite competes with a machine that is
 * already saturated, and the suite's own workers then starve each other
 * as well.  Two cores held back is enough to keep the run scheduled.
 *
 * Override with VITEST_MAX_WORKERS on a machine that is genuinely idle
 * (or in CI, where the container is usually the only tenant).
 */
const MAX_WORKERS =
  Number(process.env.VITEST_MAX_WORKERS) || Math.max(1, cpus().length - 2);

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

    // ── Timeouts sized for a CONTENDED machine ──────────────────────
    //
    // Vitest's defaults (testTimeout 5s, hookTimeout 10s) assume the
    // suite has the box to itself.  Measured on this repo when idle:
    // 49 files, `environment` 102s total — ~2.1s of jsdom construction
    // per file — plus ~0.4s of setup.  Starve that by 8x (normal here,
    // where agents hold most cores) and per-file environment setup
    // alone lands near 17s, past the 10s hook budget.
    //
    // That is exactly the reported symptom: spurious failures under
    // parallel load, a DIFFERENT test each time, always green on a
    // clean re-run.  Nothing is racy — the work simply doesn't get
    // scheduled in time.
    //
    // Raising these does not hide real hangs; an infinite loop still
    // fails, just 20s later. Trading a slower red for no false reds is
    // the right side of that bargain when the false reds are frequent
    // enough to train people to re-run instead of read.
    testTimeout: 20_000,
    hookTimeout: 20_000,
    teardownTimeout: 20_000,

    // Both pools configured: 2.x defaults to `forks`, but pinning only
    // the current default would silently stop applying if that changes.
    poolOptions: {
      forks: { maxForks: MAX_WORKERS, minForks: 1 },
      threads: { maxThreads: MAX_WORKERS, minThreads: 1 },
    },
  },
});
