/**
 * Maintenance's tours — data only; the engine lives in
 * components/tour.  Words live in the locale files under
 * `tour.maintenance.*`; anchors live on the real controls as
 * `data-tour` attributes (Tasks.tsx, AddTaskDialog.tsx).
 */
import type { TourSpec } from '../../components/tour/types';

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
      // commit: the tour suggested this idea, so it does not also
      // press the trigger — the card shows the real count and hands
      // over.  countFrom reads the chip well's live selection size.
      { anchor: 'maintenance.create', advanceOn: 'click-gone',
        commit: true, countFrom: 'maintenance.vehicle-chips' },
    ],
    signals: ['maintenance_task:create'],
    // With signals: offered to the person who created five or more
    // tasks ONE AT A TIME recently — the exact person the shortcut
    // helps.  Without them (endpoint unreachable): degrade to the
    // page-local evidence, never to silence.
    relevant: (ctx) => {
      if (!ctx.canCreate) return false;
      const sig = ctx.signals?.['maintenance_task:create'];
      return sig ? sig.solo >= 5 : ctx.count >= 5;
    },
    // Already bulk-adds — trail events riding a group id ARE the bulk
    // path.  Retire the tour unseen; teaching this person costs their
    // attention and pays nothing.
    adopted: (ctx) =>
      (ctx.signals?.['maintenance_task:create']?.grouped ?? 0) > 0,
  },
];
