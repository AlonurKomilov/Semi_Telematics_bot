/**
 * Maintenance's tour shelf — the collector the catalog imports.
 *
 * Add a tour: new file beside this one, one spec, then one line here.
 * Words go in the locales under tour.maintenance.<name>.*, anchors on
 * the real controls as data-tour attributes; the guards refuse
 * anything missed (components/tour/CLAUDE.md has the full recipe).
 */
import type { TourSpec } from '../../../components/tour/types';
import { bulkAdd } from './bulkAdd';

export const MAINTENANCE_TOURS: readonly TourSpec[] = [
  bulkAdd,
];
