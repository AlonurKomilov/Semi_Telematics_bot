/**
 * Every tour the app can offer, in one map — the drift anchor.
 *
 * tour.test.ts walks this catalog and fails the build when a
 * step's anchor no longer exists in the source, or a tour's copy is
 * missing from any locale.  A tour someone forgot to register here
 * simply never runs, which is the safe failure.
 */
import { MAINTENANCE_TOURS } from '../../features/maintenance/tour';
import { isEligible, type TourState, type TourCtx, type TourSpec } from './types';

export const TOUR_CATALOG: readonly TourSpec[] = [
  ...MAINTENANCE_TOURS,
];

/** The single tour to offer on this page right now, or null. */
export function eligibleTour(
  feature: string,
  ctx: TourCtx,
  state: TourState,
): TourSpec | null {
  for (const spec of TOUR_CATALOG) {
    if (spec.feature === feature && isEligible(spec, ctx, state)) return spec;
  }
  return null;
}
