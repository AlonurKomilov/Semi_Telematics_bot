/**
 * Top control bar — view-mode toggle (left) + date-range presets (right).
 *
 * Replaces the old Pending/History tab strip.  View-mode toggle and
 * date range are the two page-level decisions the operator makes;
 * everything below (filters, severity chips, search) refines the
 * result inside that scope.  The History tab was removed because the
 * date range already covers the lookback case — selecting "30d"
 * surfaces the same alerts the History tab used to show.
 *
 * Page-reset on filter change is automatic via useAlertsFilters'
 * setters — no manual setPage(1) here.
 */
import { DateRangePresets } from '../../../components/shell';
import { useAlertsFilters } from '../_shared/useAlertsFilters';
import { useAlertsQuery } from '../_shared/useAlertsQuery';

export default function AlertsControlBar() {
  const { viewMode, setViewMode, days, setDays } = useAlertsFilters();
  const { isFetching } = useAlertsQuery();

  return (
    <div className="flex items-center justify-between mb-4 border-b border-border pb-3">
      <div className="flex gap-1" role="group" aria-label="View mode">
        {(['by-vehicle', 'list'] as const).map((mode) => (
          <button
            key={mode}
            onClick={() => setViewMode(mode)}
            className={`px-4 py-2 rounded-md text-sm font-medium transition border ${
              viewMode === mode
                ? 'bg-primary text-primary-foreground border-primary'
                : 'bg-muted/40 text-muted-foreground border-border hover:bg-muted hover:text-foreground'
            }`}
          >
            {mode === 'by-vehicle' ? 'Per vehicle' : 'Per alert'}
          </button>
        ))}
      </div>
      <DateRangePresets
        value={days}
        onChange={(d) => setDays(d)}
        isFetching={isFetching}
      />
    </div>
  );
}
