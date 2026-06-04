/**
 * Re-export of the Pattern B overview page.
 *
 * The implementation moved to ``features/overview/Overview.tsx``
 * during the Phase 4 Pattern B migration.  This file stays as a thin
 * re-export so the router import path (``pages/Overview``) keeps
 * working without touching ``router.tsx``.
 */
export { default } from '../features/overview/Overview';
