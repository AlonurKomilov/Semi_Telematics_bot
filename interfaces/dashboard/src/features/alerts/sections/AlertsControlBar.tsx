/**
 * Top control bar — the date-range presets.
 *
 * The date window is the page-level decision: it chooses WHICH alerts are
 * fetched, and everything below (status / type / severity chips, vehicle
 * search) refines within that scope.  Replaces the old Pending/History tab
 * strip — the range already covers the lookback case, since selecting
 * "30d" surfaces the same alerts History used to show.
 *
 * A "Per vehicle / Per alert" toggle also used to live here; see the note
 * in the body for why it's gone.
 *
 * Page-reset on filter change is automatic via useAlertsFilters'
 * setters — no manual setPage(1) here.
 */
import { DateRangePresets } from '../../../components/shell';
import { useAlertsFilters } from '../_shared/useAlertsFilters';
import { useAlertsQuery } from '../_shared/useAlertsQuery';

export default function AlertsControlBar() {
  const { days, setDays, ackState } = useAlertsFilters();
  const { isFetching } = useAlertsQuery();
  // The open queue is unwindowed by design (an unacknowledged alert is open
  // regardless of age), so the range picker genuinely does nothing here.
  // Disabled WITH THE REASON rather than hidden: a control that vanishes
  // between states is harder to learn than one that explains itself.
  const windowApplies = ackState !== 'active';

  // The old "Per vehicle / Per alert" toggle lived here.  It was a
  // hardcoded special case of something the grid already does better:
  // every column's ⋮ menu offers "Group rows by this", so an operator can
  // group by Vehicle, Type, Severity or Company — and see it as a
  // removable "Grouped by …" chip.  Both modes had long since read the
  // same flat endpoint, so the toggle only set a default grouping, which
  // a saved per-user preference could silently override.
  return (
    <div className="flex items-center justify-end gap-2 mb-4 border-b border-border pb-3">
      {!windowApplies && (
        <span className="text-2xs text-muted-foreground">
          Open alerts are never hidden by age
        </span>
      )}
      <DateRangePresets
        value={days}
        onChange={(d) => setDays(d)}
        isFetching={isFetching}
        disabled={!windowApplies}
      />
    </div>
  );
}
