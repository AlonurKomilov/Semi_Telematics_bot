/**
 * Maintenance's tours — data only; the engine lives in
 * components/spotlight.  Words live in the locale files under
 * `spotlight.maintenance.*`; anchors live on the real controls as
 * `data-spotlight` attributes (Tasks.tsx, AddTaskDialog.tsx).
 */
import type { TourSpec } from '../../components/spotlight/types';

export const MAINTENANCE_TOURS: readonly TourSpec[] = [
  {
    // One task, many vehicles — the bulk path people miss while adding
    // the same oil change truck by truck.
    key: 'maintenance.bulk_add',
    feature: 'maintenance',
    steps: [
      { anchor: 'maintenance.new-task', advanceOn: 'click' },
      { anchor: 'maintenance.multi-toggle', advanceOn: 'click' },
      // A chip, not the well: its padding and 'Loading…' text are
      // inside the anchor too, and touching those picks nothing.
      { anchor: 'maintenance.vehicle-chips', advanceOn: 'click', advanceWithin: 'button' },
      // click-gone: Create can be REFUSED (Description is required —
      // a live run hit exactly that while the old click-advance was
      // already congratulating).  The form closes itself on success,
      // so the anchor leaving the DOM is the honest completion signal.
      { anchor: 'maintenance.create', advanceOn: 'click-gone' },
    ],
    // Offered once the page shows real use — a handful of tasks says
    // "this person adds tasks", and that is who the shortcut helps.
    // An empty account gets the EmptyState's own onboarding instead.
    relevant: (ctx) => ctx.canCreate && ctx.count >= 5,
  },
];
