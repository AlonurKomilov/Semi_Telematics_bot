/**
 * Filter chip row — ack-state + type + severity + vehicle search.
 *
 * These are SERVER filters: each one re-queries, so it narrows the whole
 * dataset.  That's why the grid below has no column filters of its own —
 * a client filter would scope to the loaded batch (capped at one fetch)
 * and silently disagree with these whenever the window overflows.  One
 * control per dimension, and the correct one.
 *
 * NEXT STEP (deferred): move these INTO the grid — column filters that
 * write these same URL params.  That gets one control per dimension AND
 * puts it where the data is, and it's the prerequisite for turning on
 * DataGrid's saved tabs here (tabs capture column filters, so today they'd
 * have nothing to save).  It needs DataGrid to support server-driven
 * filters, so it waits for that.
 *
 * All four reads + writes go through useAlertsFilters (URL params).
 * Page-reset on filter change is automatic.
 *
 * The one compound setter that needs caller-side wiring: changing
 * ack-state ALSO clears the selection set, because the rows that
 * were selected may no longer be ackable (e.g. switching from
 * 'active' to 'acknowledged' surfaces history rows that the bulk-ack
 * button shouldn't include).
 */
import { useTranslation } from 'react-i18next';
import { FilterBar, FilterChips } from '../../../components/shell';
import { useAlertsFilters } from '../_shared/useAlertsFilters';
import { useAlertsSelection } from '../_shared/AlertsSelectionContext';

const ALERT_TYPES = ['all', 'fault', 'health', 'fuel', 'events', 'parking'] as const;
const ACK_STATES = ['active', 'acknowledged', 'all'] as const;
const ACK_STATE_LABELS: Record<(typeof ACK_STATES)[number], string> = {
  active: 'Not acknowledged',
  acknowledged: 'Acknowledged',
  all: 'All',
};

export default function AlertsFilterChips() {
  const { t } = useTranslation();
  const {
    typeFilter,
    setTypeFilter,
    severityFilter,
    setSeverityFilter,
    ackState,
    setAckState,
    vehicleSearch,
    setVehicleSearch,
  } = useAlertsFilters();
  const { clearSelection } = useAlertsSelection();

  return (
    <FilterBar>
      {/* Status (ack-state) chips live inline with the type /
          severity filters — one flat row of refinements.  Default
          'Not acknowledged' is the daily work queue; 'Acknowledged'
          / 'All' use the date range to bound the lookback.
          Each group is LABELLED: all three are exclusive and each
          carries its own "All", so unlabelled neighbours read as
          "All … All …" — one muddled selection instead of three
          independent dimensions. */}
      <FilterChips
        label="Status"
        options={ACK_STATES}
        value={ackState}
        onChange={(v) => {
          setAckState(v);
          clearSelection();
        }}
        labelFor={(v) => ACK_STATE_LABELS[v]}
      />
      <FilterChips
        label="Type"
        options={ALERT_TYPES}
        value={typeFilter as (typeof ALERT_TYPES)[number]}
        onChange={(v) => setTypeFilter(v)}
      />
      {/* Severity filter — pushed to SQL via the `severity` query
          param so server-side pagination stays accurate (filtering
          client-side broke "Page N of M" when a page contained no
          matches and rendered empty). */}
      <FilterChips
        label="Severity"
        options={['all', 'critical', 'warning', 'info'] as const}
        value={severityFilter}
        onChange={(v) => setSeverityFilter(v)}
      />
      <input
        type="text"
        placeholder={t(
          'alerts.vehicle_search_hint',
          'Search vehicle (e.g. "237" or "Truck 4")',
        )}
        value={vehicleSearch}
        onChange={(e) => setVehicleSearch(e.target.value)}
        className="bg-background border border-border rounded-md px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:border-ring w-64"
        title='Substring match on vehicle name — e.g. typing "23" finds Truck 237 and Trailer 1230'
      />
    </FilterBar>
  );
}
