/**
 * Spotlight — interactive product tours (shared chrome, any feature).
 *
 *   SpotlightHost   the one line a page adds — offers + runs its tours
 *   Tour data lives per feature in `features/<x>/spotlights.ts`;
 *   registration in `spotlightCatalog.ts`; words in the locales under
 *   `spotlight.*`; anchors on real controls as `data-spotlight`.
 */
export { default as SpotlightHost } from './SpotlightHost';
export { SPOTLIGHT_CATALOG, eligibleTour } from './spotlightCatalog';
export { isEligible, SNOOZE_DAYS } from './types';
export type { TourSpec, TourStep, TourCtx, SpotlightState } from './types';
