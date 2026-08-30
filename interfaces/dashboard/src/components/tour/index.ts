/**
 * Tour — interactive product tours (shared chrome, any feature).
 *
 *   TourHost   the one line a page adds — offers + runs its tours
 *   Tour data lives per feature in `features/<x>/tours.ts`;
 *   registration in `tourCatalog.ts`; words in the locales under
 *   `tour.*`; anchors on real controls as `data-tour`.
 */
export { default as TourHost } from './TourHost';
export { TOUR_CATALOG, eligibleTour } from './tourCatalog';
export { isEligible, SNOOZE_DAYS } from './types';
export type { TourSpec, TourStep, TourCtx, TourState } from './types';
