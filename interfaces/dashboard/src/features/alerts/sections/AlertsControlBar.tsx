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
import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from '../../../lib/toast';
import { Eye } from 'lucide-react';

import { DateRangePresets } from '../../../components/shell';
import { apiJSON } from '../../../api/client';
import { useAlertsFilters } from '../_shared/useAlertsFilters';
import { useAlertsQuery, buildAlertsFilterParams } from '../_shared/useAlertsQuery';
import { useAlertSegmentCounts } from '../_shared/useAlertSegmentCounts';

export default function AlertsControlBar() {
  const {
    days, setDays, ackState, typeFilter, severityFilter, vehicleSearch, sort, dir,
  } = useAlertsFilters();
  const { isFetching } = useAlertsQuery();
  const { counts } = useAlertSegmentCounts();
  const qc = useQueryClient();
  const [clearing, setClearing] = useState(false);
  // The window bounds RESOLVED history per-row (open rows are never
  // hidden by age — see _alert_filter_clause), and under the seen/working
  // tabs every tab can hold open rows.  The picker therefore always
  // applies to the resolved part of what is shown; the note below keeps
  // saying the half that does not move.
  const windowApplies = ackState !== 'new';

  // The old "Per vehicle / Per alert" toggle lived here.  It was a
  // hardcoded special case of something the grid already does better:
  // every column's ⋮ menu offers "Group rows by this", so an operator can
  // group by Vehicle, Type, Severity or Company — and see it as a
  // removable "Grouped by …" chip.  Both modes had long since read the
  // same flat endpoint, so the toggle only set a default grouping, which
  // a saved per-user preference could silently override.
  // The backlog's exit door, offered only where the backlog is felt: on
  // New, with something in it.  The passive ledger drains a row when it
  // crosses a screen, which is the right default and no answer at all to
  // an inherited 4,583 — 183 pages of scrolling to reach a clean tab.
  //
  // It CONFIRMS, and says the part people would not think of: the Seen
  // column is account-wide, so this writes your name where colleagues
  // read it as coverage.  That is a real cost, and the honest response is
  // to state it rather than to withhold the control.
  const unseen = counts?.new ?? 0;
  const offerClearAll = ackState === 'new' && unseen > 0;

  const markAllSeen = async () => {
    if (clearing) return;
    if (!window.confirm(
      `Mark ${unseen.toLocaleString()} alert${unseen === 1 ? '' : 's'} as seen?\n\n`
      + 'They leave your New tab, and your name goes on them in the Seen '
      + 'column — where teammates read it as "someone has looked at this".')) return;
    setClearing(true);
    try {
      const params = buildAlertsFilterParams({
        typeFilter, severityFilter, vehicleSearch, ackState, days, sort, dir,
      });
      const r = await apiJSON<{ marked: number }>(
        `/alerts/seen/all?${params.toString()}`, { method: 'POST' });
      toast.success(`Marked ${r.marked.toLocaleString()} as seen`);
      await qc.invalidateQueries({ queryKey: ['alerts'] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Couldn’t mark these seen');
    } finally {
      setClearing(false);
    }
  };

  return (
    <div className="flex items-center justify-end gap-2 mb-4 border-b border-border pb-3">
      {offerClearAll && (
        <button
          type="button"
          onClick={() => { void markAllSeen(); }}
          aria-busy={clearing || undefined}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 min-h-tap rounded-md
                     text-xs font-medium border border-border text-muted-foreground
                     hover:text-foreground transition-colors mr-auto"
        >
          <Eye className="size-3.5" aria-hidden />
          Mark all {unseen.toLocaleString()} as seen
        </button>
      )}
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
