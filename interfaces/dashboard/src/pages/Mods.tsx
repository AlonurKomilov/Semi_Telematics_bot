/**
 * Route shell. The page itself lives in the mods service — `pages/` is
 * the router's entry point, not where a feature keeps its screens — and
 * comes through the barrel like every other consumer (mods/index.test.ts
 * fails the build on a deep import from outside `mods/`).
 */
export { ModsPage as default } from '../mods';
