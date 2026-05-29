import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, CheckCircle2, ChevronLeft, ChevronRight, ChevronDown } from 'lucide-react';
import { apiJSON } from '../../api/client';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
  LastUpdated,
  FilterBar,
  FilterChips,
  DateRangePresets,
} from '../../components/shell';
import type {
  Alert,
  AlertsResponse,
  BulkAckResponse,
  VehiclesAlertsResponse,
} from '../../types';
import { formatAlertDescription } from '../../utils/alertDescription';

const ALERT_TYPES = ['all', 'fault', 'health', 'fuel', 'events', 'parking'] as const;
type AlertType = typeof ALERT_TYPES[number];

// Ack-state chip → server ``ack_state`` param.  Default 'active' so the
// page lands on the un-acked work queue; the other two reveal history.
const ACK_STATES = ['active', 'acknowledged', 'all'] as const;
type AckState = typeof ACK_STATES[number];
const ACK_STATE_LABELS: Record<AckState, string> = {
  active: 'Not acknowledged',
  acknowledged: 'Acknowledged',
  all: 'All',
};

function TypeBadge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    fault: 'bg-orange-500/15 text-orange-700 dark:text-orange-400',
    health: 'bg-red-500/15 text-red-700 dark:text-red-400',
    fuel: 'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400',
    events: 'bg-purple-500/15 text-purple-700 dark:text-purple-400',
    parking: 'bg-cyan-500/15 text-cyan-700 dark:text-cyan-400',
  };
  const cls = colors[type] || 'bg-gray-500/20 text-muted-foreground';
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>{type}</span>;
}

// Severity dot + label.  Reads alert_history.severity (server-authoritative)
// so it always matches the bot's per-type formatter.  Falls back to 'warning'
// for legacy rows that haven't been migrated yet.
function SeverityDot({ severity }: { severity?: string }) {
  const sev = (severity === 'critical' || severity === 'info') ? severity : 'warning';
  const cfg: Record<string, { dot: string; text: string; label: string }> = {
    critical: { dot: 'bg-red-500',    text: 'text-red-600 dark:text-red-400',     label: 'Critical' },
    warning:  { dot: 'bg-orange-500', text: 'text-orange-600 dark:text-orange-400', label: 'Warning' },
    info:     { dot: 'bg-blue-500',   text: 'text-blue-600 dark:text-blue-400',   label: 'Info' },
  };
  const c = cfg[sev];
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${c.text}`}>
      <span className={`w-2 h-2 rounded-full ${c.dot}`} aria-hidden />
      {c.label}
    </span>
  );
}

function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

// Only un-acknowledged (status='active') alerts can be acknowledged —
// re-acking a cleared alert is a no-op.  Selection + the bulk-ack
// count are scoped to ackable rows so "Acknowledge (N)" always means
// "N alerts that still need it", even in the All / Acknowledged views.
function isAckable(a: Alert): boolean {
  return (a.status ?? 'active') === 'active';
}

// Resolution marker for a non-active alert.  A human ack carries a
// name (acknowledged_by > 0, resolved server-side to a display name);
// a self-cleared alert has no actor and reads "Auto-resolved".  Active
// alerts render nothing here.
function AckMarker({ alert }: { alert: Alert }) {
  if ((alert.status ?? 'active') === 'active') return null;
  const human = (alert.acknowledged_by ?? 0) > 0;
  const when = alert.acknowledged_at
    ? new Date(alert.acknowledged_at).toLocaleString()
    : '';
  if (human) {
    const who = alert.acknowledged_by_name || 'user';
    return (
      <span
        className="inline-flex items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400"
        title={when ? `Acknowledged ${when}` : undefined}
      >
        <CheckCircle2 size={13} aria-hidden />
        Acknowledged by {who}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground"
      title={when ? `Auto-resolved ${when}` : undefined}
    >
      <CheckCircle2 size={13} aria-hidden />
      Auto-resolved
    </span>
  );
}

export default function Alerts() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  // Per-vehicle vs flat-list view.  Persisted to localStorage so the
  // operator's last choice survives navigation.  Per-vehicle is the
  // default landing view because most fleets large enough to need
  // this page have many alerts per vehicle — flat-list rewards
  // dispatchers triaging a single fire, by-vehicle rewards anyone
  // working through chronic offenders.
  const [viewMode, setViewMode] = useState<'list' | 'by-vehicle'>(
    () => {
      try {
        const stored = localStorage.getItem('alerts.viewMode');
        return stored === 'list' ? 'list' : 'by-vehicle';
      } catch {
        return 'by-vehicle';
      }
    },
  );
  const [expandedVehicles, setExpandedVehicles] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string | number>>(new Set());
  const [typeFilter, setTypeFilter] = useState<AlertType>('all');
  const [severityFilter, setSeverityFilter] = useState<
    'all' | 'critical' | 'warning' | 'info'
  >('all');
  const [vehicleSearch, setVehicleSearch] = useState('');
  const [days, setDays] = useState(30);
  // Ack-state chip — lands on 'active' (un-acked work queue); the
  // 'acknowledged' / 'all' options reveal the windowed history.
  const [ackState, setAckState] = useState<AckState>('active');
  const [bulkError, setBulkError] = useState('');
  const [acking, setAcking] = useState(false);
  // Server-side pagination — 100 in both views keeps the page count
  // consistent regardless of toggle position and matches what most
  // dispatchers expect ("show me 100 at a time").
  const PAGE_SIZE_LIST = 100;
  const PAGE_SIZE_VEHICLES = 100;
  const [page, setPage] = useState(1);

  // Use the vehicle-grouped endpoint when in per-vehicle view — the
  // server aggregates by vehicle so the page count reflects vehicle
  // cards (e.g. ``Page 1 of 2`` for 80 trucks) instead of underlying
  // alert rows (which would read ``Page 1 of 22`` for 2164 alerts
  // and confuse the user about what they're stepping through).
  const useVehicleEndpoint = viewMode === 'by-vehicle';
  const pageSize = useVehicleEndpoint ? PAGE_SIZE_VEHICLES : PAGE_SIZE_LIST;

  const queryKey = [
    'alerts', viewMode, typeFilter, severityFilter,
    vehicleSearch, ackState, page, days,
  ] as const;
  const {
    data,
    isLoading: loading,
    isFetching,
    error: queryError,
    refetch,
    dataUpdatedAt,
  } = useQuery<AlertsResponse | VehiclesAlertsResponse>({
    queryKey,
    queryFn: () => {
      const params = new URLSearchParams();
      if (typeFilter !== 'all') params.set('alert_type', typeFilter);
      if (severityFilter !== 'all') params.set('severity', severityFilter);
      if (vehicleSearch) params.set('vehicle', vehicleSearch);
      params.set('ack_state', ackState);
      params.set('days', String(days));
      params.set('page_size', String(pageSize));
      params.set('page', String(page));
      const path = useVehicleEndpoint
        ? '/alerts/pending/by-vehicle'
        : '/alerts/pending';
      const qs = params.toString();
      return apiJSON<AlertsResponse | VehiclesAlertsResponse>(
        `${path}${qs ? `?${qs}` : ''}`,
      );
    },
    placeholderData: (prev) => prev,
  });

  // Discriminate the response shape by what the payload actually
  // contains, not by ``useVehicleEndpoint`` alone — react-query
  // hands back the previous response as ``placeholderData`` while a
  // toggle is mid-flight, so right after the user flips to
  // per-vehicle the ``data`` object can still be the old
  // ``{ alerts: [...] }`` shape.  Casting that to a vehicles shape
  // and calling ``.map()`` on the missing ``vehicles`` prop blew up
  // the page (TypeError in the useMemo below).
  const vehiclesData =
    data && Array.isArray((data as VehiclesAlertsResponse).vehicles)
      ? (data as VehiclesAlertsResponse)
      : undefined;
  const alertsData =
    data && Array.isArray((data as AlertsResponse).alerts)
      ? (data as AlertsResponse)
      : undefined;
  const alerts: Alert[] = alertsData?.alerts
    ?? (vehiclesData?.vehicles ?? []).flatMap((v) => v.alerts ?? []);

  // Map the server-aggregated shape onto the group struct the JSX
  // expects.  When we're on the flat /pending fetch (list view) we
  // still derive vehicleGroups client-side off the same alerts
  // array so the expand affordance keeps working — used if the user
  // ever flips to per-vehicle while the previous /pending payload
  // is still hanging around as react-query placeholderData.
  const vehicleGroups = useMemo(() => {
    if (vehiclesData) {
      const sevRank: Record<string, number> = { critical: 0, warning: 1, info: 2 };
      return (vehiclesData.vehicles ?? []).map((v) => {
        const vAlerts = v.alerts ?? [];
        const types = new Set<string>();
        for (const a of vAlerts) if (a.alert_type) types.add(a.alert_type);
        const max_severity =
          (v.critical_count ?? 0) > 0 ? 'critical'
          : (v.warning_count ?? 0) > 0 ? 'warning'
          : 'info';
        return {
          key: v.vehicle_id || v.vehicle_name || 'unknown',
          vehicle_name: v.vehicle_name || v.vehicle_id || 'Unknown',
          vehicle_id: v.vehicle_id || '',
          alerts: vAlerts,
          counts: {
            critical: v.critical_count ?? 0,
            warning: v.warning_count ?? 0,
            info: v.info_count ?? 0,
          },
          types,
          max_severity,
          latest_seen: v.latest_seen || '',
          _sevRank: sevRank[max_severity] ?? 3,
        };
      });
    }
    // Client-side grouping for the History flat-fetch path.
    const sevRank: Record<string, number> = { critical: 0, warning: 1, info: 2 };
    const groups = new Map<string, {
      key: string;
      vehicle_name: string;
      vehicle_id: string;
      alerts: Alert[];
      counts: { critical: number; warning: number; info: number };
      types: Set<string>;
      max_severity: string;
      latest_seen: string;
    }>();
    for (const a of alerts) {
      const key = (a.vehicle_id || a.vehicle_name || 'unknown') as string;
      let g = groups.get(key);
      if (!g) {
        g = {
          key,
          vehicle_name: a.vehicle_name || a.vehicle_id || 'Unknown',
          vehicle_id: a.vehicle_id || '',
          alerts: [],
          counts: { critical: 0, warning: 0, info: 0 },
          types: new Set<string>(),
          max_severity: 'info',
          latest_seen: '',
        };
        groups.set(key, g);
      }
      g.alerts.push(a);
      const sev = (a.severity ?? 'warning') as 'critical' | 'warning' | 'info';
      g.counts[sev] = (g.counts[sev] ?? 0) + 1;
      if (sevRank[sev] < sevRank[g.max_severity]) g.max_severity = sev;
      if (a.alert_type) g.types.add(a.alert_type);
      const seen = a.last_seen || a.created_at || '';
      if (seen > g.latest_seen) g.latest_seen = seen;
    }
    return Array.from(groups.values()).sort((a, b) => {
      const rd = sevRank[a.max_severity] - sevRank[b.max_severity];
      if (rd !== 0) return rd;
      return (b.latest_seen || '').localeCompare(a.latest_seen || '');
    });
  }, [alerts, vehiclesData]);

  const fetchError = queryError instanceof Error ? queryError.message : '';

  function setViewModePersist(v: 'list' | 'by-vehicle') {
    setViewMode(v);
    setPage(1);  // resetting on toggle keeps "Page N of M" honest
    try { localStorage.setItem('alerts.viewMode', v); } catch { /* ignore quota errors */ }
  }

  function toggleVehicleExpanded(key: string) {
    setExpandedVehicles((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  function selectAllForVehicle(group: typeof vehicleGroups[number], on: boolean) {
    // Only the un-acked alerts in this vehicle are selectable.
    setSelected((prev) => {
      const next = new Set(prev);
      for (const a of group.alerts) {
        if (!isAckable(a)) continue;
        if (on) next.add(a.id); else next.delete(a.id);
      }
      return next;
    });
  }

  async function ackSelected() {
    if (selected.size === 0) return;
    setAcking(true);
    try {
      const ids = Array.from(selected).map(Number);
      await apiJSON<BulkAckResponse>('/alerts/bulk-ack', {
        method: 'POST',
        body: { ids },
      });
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ['alerts'] });
    } catch (e) {
      setBulkError(e instanceof Error ? e.message : 'Bulk acknowledge failed');
    } finally {
      setAcking(false);
    }
  }

  function toggleSelect(id: string | number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  return (
    <div>
      <PageHeader
        icon={Bell}
        title={t('alerts.page_title')}
        description={t('alerts.page_description_pending')}
        actions={
          <div className="flex items-center gap-3">
            {selected.size > 0 && (
              <button
                onClick={ackSelected}
                disabled={acking}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded-md text-xs font-medium text-primary-foreground transition"
              >
                <CheckCircle2 size={13} />
                {acking ? t('alerts.acknowledging') : t('alerts.acknowledge_n', { n: selected.size })}
              </button>
            )}
            <LastUpdated
              fetchedAt={dataUpdatedAt}
              isFetching={isFetching}
              onRefresh={refetch}
            />
          </div>
        }
      />

      {/* Top control bar — replaces the old Pending/History tab strip.
          View-mode toggle (left) and date range (right) are the two
          page-level decisions the operator makes; everything below
          (filters, severity chips, search) refines the result inside
          that scope.  The History tab was removed because the date
          range already covers the lookback case — selecting "30d"
          surfaces the same alerts the History tab used to show. */}
      <div className="flex items-center justify-between mb-4 border-b border-border pb-3">
        <div className="flex gap-1" role="group" aria-label="View mode">
          {(['by-vehicle', 'list'] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setViewModePersist(mode)}
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
          onChange={(d) => { setDays(d); setPage(1); }}
          isFetching={isFetching}
        />
      </div>

      <FilterBar>
        {/* Status (ack-state) chips live inline with the type /
            severity filters — one flat row of refinements.  Default
            'Not acknowledged' is the daily work queue; 'Acknowledged'
            / 'All' use the date range to bound the lookback. */}
        <FilterChips
          options={ACK_STATES}
          value={ackState}
          onChange={(v) => { setAckState(v); setSelected(new Set()); setPage(1); }}
          labelFor={(v) => ACK_STATE_LABELS[v]}
        />
        <FilterChips
          options={ALERT_TYPES}
          value={typeFilter}
          onChange={(v) => { setTypeFilter(v); setPage(1); }}
        />
        {/* Severity filter — pushed to SQL via the `severity` query
            param so server-side pagination stays accurate (filtering
            client-side broke "Page N of M" when a page contained no
            matches and rendered empty). */}
        <FilterChips
          options={['all', 'critical', 'warning', 'info'] as const}
          value={severityFilter}
          onChange={(v) => { setSeverityFilter(v); setPage(1); }}
        />
        <input
          type="text"
          placeholder={t('alerts.vehicle_search_hint', 'Search vehicle (e.g. "237" or "Truck 4")')}
          value={vehicleSearch}
          onChange={(e) => { setVehicleSearch(e.target.value); setPage(1); }}
          className="bg-background border border-border rounded-md px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:border-ring w-64"
          title="Substring match on vehicle name — e.g. typing '23' finds Truck 237 and Trailer 1230"
        />
      </FilterBar>

      {bulkError && (
        <div className="mb-3">
          <ErrorState message={bulkError} />
        </div>
      )}

      {fetchError && alerts.length === 0 ? (
        <ErrorState
          title="Couldn't load alerts"
          message={fetchError}
          onRetry={() => refetch()}
        />
      ) : loading && alerts.length === 0 ? (
        <TableSkeleton rows={6} cols={5} />
      ) : alerts.length === 0 ? (
        <EmptyState
          icon={Bell}
          title={
            ackState === 'active'
              ? 'No pending alerts'
              : ackState === 'acknowledged'
                ? 'No acknowledged alerts in this window'
                : 'No alerts in this window'
          }
          description={
            ackState === 'active'
              ? 'You\'re all caught up — every alert has been acknowledged.'
              : 'Try widening the date range or removing filters.'
          }
        />
      ) : viewMode === 'by-vehicle' ? (
        <div className="rounded-lg border border-border overflow-hidden">
          {/* Column-aligned header row — the per-vehicle cards below
              use the same grid template so labels line up like a
              table.  Only fields accurate at the *vehicle group*
              level are surfaced: name, alert types present,
              severity tallies, latest activity.  Per-alert detail
              (id, description) lives in the expanded row. */}
          {(() => {
            // Flatten only the *ackable* (un-acked) alert ids across
            // the visible vehicle cards — the header checkbox selects
            // alerts that still need acknowledging, never the already-
            // cleared ones (re-acking them is a no-op).
            const pageAlertIds = vehicleGroups
              .flatMap((g) => g.alerts)
              .filter(isAckable)
              .map((a) => a.id);
            const totalOnPage = pageAlertIds.length;
            const selectedOnPage = pageAlertIds.filter((id) => selected.has(id)).length;
            const allOnPageSelected = totalOnPage > 0 && selectedOnPage === totalOnPage;
            const someOnPageSelected = selectedOnPage > 0 && selectedOnPage < totalOnPage;
            return (
              <div className="hidden md:grid grid-cols-[2rem_1.25rem_minmax(8rem,1.4fr)_minmax(10rem,1.6fr)_minmax(14rem,2fr)_minmax(11rem,1fr)] gap-3 items-center px-4 py-2.5 bg-card text-xs font-semibold uppercase tracking-wide text-muted-foreground border-b border-border">
                <input
                  type="checkbox"
                  checked={allOnPageSelected}
                  disabled={totalOnPage === 0}
                  ref={(el) => {
                    if (el) el.indeterminate = someOnPageSelected;
                  }}
                  onChange={() => {
                    if (allOnPageSelected) {
                      // Clear every id that belongs to this page; leave
                      // any out-of-page selection (rare but possible
                      // after pagination) untouched.
                      setSelected((prev) => {
                        const next = new Set(prev);
                        for (const id of pageAlertIds) next.delete(id);
                        return next;
                      });
                    } else {
                      setSelected((prev) => {
                        const next = new Set(prev);
                        for (const id of pageAlertIds) next.add(id);
                        return next;
                      });
                    }
                  }}
                  aria-label="Select all un-acknowledged alerts on this page"
                />
                <span aria-hidden />
                <span>Vehicle</span>
                <span>Alert types</span>
                <span>Severity</span>
                <span>Last seen</span>
              </div>
            );
          })()}
          <div className="divide-y divide-border">
            {vehicleGroups.map((g) => {
              // Selection state is scoped to the vehicle's ackable alerts.
              const ackable = g.alerts.filter(isAckable);
              const allSelected = ackable.length > 0 && ackable.every((a) => selected.has(a.id));
              const someSelected = ackable.some((a) => selected.has(a.id));
              const expanded = expandedVehicles.has(g.key);
              const sevBadgeBase = "px-2 py-0.5 rounded-full text-xs font-medium";
              return (
                <div key={g.key} className="bg-card">
                  <div className="grid grid-cols-[2rem_1.25rem_minmax(8rem,1.4fr)_minmax(10rem,1.6fr)_minmax(14rem,2fr)_minmax(11rem,1fr)] gap-3 items-center px-4 py-3 hover:bg-muted/40 transition">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      disabled={ackable.length === 0}
                      ref={(el) => {
                        // Indeterminate when only part of this vehicle's
                        // ackable alerts are selected — the bulk-ack will
                        // close a subset, not the whole truck.
                        if (el) el.indeterminate = someSelected && !allSelected;
                      }}
                      onChange={() => selectAllForVehicle(g, !allSelected)}
                      aria-label={`Select un-acknowledged alerts for ${g.vehicle_name}`}
                    />
                    <button
                      onClick={() => toggleVehicleExpanded(g.key)}
                      aria-label={expanded ? 'Collapse' : 'Expand'}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <ChevronDown
                        className={`w-4 h-4 transition-transform ${expanded ? '' : '-rotate-90'}`}
                        aria-hidden
                      />
                    </button>
                    <span className="font-medium truncate">{g.vehicle_name}</span>
                    <div className="flex gap-1 flex-wrap">
                      {Array.from(g.types).map((tp) => (
                        <TypeBadge key={tp} type={tp} />
                      ))}
                    </div>
                    <div className="flex gap-1.5 flex-wrap">
                      {g.counts.critical > 0 && (
                        <span className={`${sevBadgeBase} bg-red-500/15 text-red-700 dark:text-red-400`}>
                          {g.counts.critical} critical
                        </span>
                      )}
                      {g.counts.warning > 0 && (
                        <span className={`${sevBadgeBase} bg-orange-500/15 text-orange-700 dark:text-orange-400`}>
                          {g.counts.warning} warning
                        </span>
                      )}
                      {g.counts.info > 0 && (
                        <span className={`${sevBadgeBase} bg-blue-500/15 text-blue-700 dark:text-blue-400`}>
                          {g.counts.info} info
                        </span>
                      )}
                    </div>
                    <span className="text-xs text-muted-foreground whitespace-nowrap">
                      {g.latest_seen ? new Date(g.latest_seen).toLocaleString() : '—'}
                    </span>
                  </div>
                  {expanded && (
                    <div className="border-t border-border bg-muted/20">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            <th className="px-4 py-2 w-8" />
                            <th className="px-4 py-2 w-20 text-left">Alert</th>
                            <th className="px-4 py-2 w-24 text-left">Severity</th>
                            <th className="px-4 py-2 text-left">Type</th>
                            <th className="px-4 py-2 text-left">Description</th>
                            <th className="px-4 py-2 text-left">Time</th>
                            {ackState !== 'active' && <th className="px-4 py-2 text-left">Status</th>}
                          </tr>
                        </thead>
                        <tbody>
                          {g.alerts.map((a) => (
                            <tr key={a.id} className="border-t border-border hover:bg-muted/30">
                              <td className="px-4 py-2 w-8">
                                <input
                                  type="checkbox"
                                  checked={selected.has(a.id)}
                                  disabled={!isAckable(a)}
                                  onChange={() => toggleSelect(a.id)}
                                  title={isAckable(a) ? undefined : 'Already acknowledged'}
                                />
                              </td>
                              <td className="px-4 py-2 text-xs text-muted-foreground font-mono w-20">
                                #{a.id}
                              </td>
                              <td className="px-4 py-2 w-24">
                                <SeverityDot severity={a.severity} />
                              </td>
                              <td className="px-4 py-2">
                                <TypeBadge type={a.alert_type || 'unknown'} />
                              </td>
                              <td className="px-4 py-2 text-muted-foreground">
                                {truncate(formatAlertDescription(a), 80)}
                              </td>
                              <td className="px-4 py-2 text-xs text-muted-foreground whitespace-nowrap">
                                {a.last_seen ? new Date(a.last_seen).toLocaleString() : '—'}
                              </td>
                              {ackState !== 'active' && (
                                <td className="px-4 py-2">
                                  <AckMarker alert={a} />
                                </td>
                              )}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-card text-muted-foreground text-left">
                <th className="px-4 py-3 w-8">
                  {(() => {
                    // Select-all targets only the un-acked rows on this
                    // page — re-acking cleared alerts is a no-op, so the
                    // "Acknowledge (N)" count stays "N still need it".
                    const ackableIds = alerts.filter(isAckable).map((a) => a.id);
                    const selectedCount = ackableIds.filter((id) => selected.has(id)).length;
                    const allSel = ackableIds.length > 0 && selectedCount === ackableIds.length;
                    return (
                      <input
                        type="checkbox"
                        checked={allSel}
                        disabled={ackableIds.length === 0}
                        ref={(el) => {
                          if (el) el.indeterminate = selectedCount > 0 && !allSel;
                        }}
                        onChange={() => {
                          if (allSel) setSelected(new Set());
                          else setSelected(new Set(ackableIds));
                        }}
                        aria-label="Select all un-acknowledged alerts"
                      />
                    );
                  })()}
                </th>
                <th className="px-4 py-3 w-20">Alert</th>
                <th className="px-4 py-3 w-24">Severity</th>
                <th className="px-4 py-3">Vehicle</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Description</th>
                <th className="px-4 py-3">Location</th>
                <th className="px-4 py-3">Time</th>
                {ackState !== 'active' && <th className="px-4 py-3">Status</th>}
              </tr>
            </thead>
            <tbody>
              {alerts.map((a) => (
                <tr key={a.id} className="border-t border-border hover:bg-muted/50">
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selected.has(a.id)}
                      disabled={!isAckable(a)}
                      onChange={() => toggleSelect(a.id)}
                      title={isAckable(a) ? undefined : 'Already acknowledged'}
                    />
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground font-mono">
                    #{a.id}
                  </td>
                  <td className="px-4 py-3">
                    <SeverityDot severity={a.severity} />
                  </td>
                  <td className="px-4 py-3">{a.vehicle_name}</td>
                  <td className="px-4 py-3">
                    <TypeBadge type={a.alert_type || 'unknown'} />
                    {/* Occurrence-count badge — "× 5" when this same
                        logical alert has fired multiple times without
                        being cleared.  Hidden for first-time alerts. */}
                    {(a.occurrence_count ?? 1) > 1 && (
                      <span
                        className="ml-2 inline-block px-2 py-0.5 rounded-full text-xs font-bold bg-orange-500/15 text-orange-500"
                        title={t('alerts.total_occurrences')}
                      >
                        × {a.occurrence_count}
                      </span>
                    )}
                  </td>
                  {/* Description — friendly sentence rendered by the
                      shared formatter so dispatchers don't have to
                      decode ``parking:unsafe:8h`` / ``fuel:19`` / raw
                      event-IDs.  Raw ``last_detail`` stays available
                      on hover for support follow-ups. */}
                  <td
                    className="px-4 py-3 text-sm text-muted-foreground max-w-xs"
                    title={(a as Alert & { last_detail?: string }).last_detail || (a as Alert & { message?: string }).message || ''}
                  >
                    {truncate(
                      formatAlertDescription(a as Alert & { last_detail?: string; message?: string }),
                      80,
                    )}
                  </td>
                  {/* Location snapshot from alert_history.location.
                      Empty when the truck didn't have GPS at first fire. */}
                  <td
                    className="px-4 py-3 text-sm text-muted-foreground max-w-[14rem] truncate"
                    title={a.location || ''}
                  >
                    {a.location ? truncate(a.location, 30) : '—'}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {a.last_seen
                      ? new Date(a.last_seen).toLocaleString()
                      : a.created_at ? new Date(a.created_at).toLocaleString() : '—'}
                  </td>
                  {ackState !== 'active' && (
                    <td className="px-4 py-3">
                      <AckMarker alert={a} />
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination footer — Next/Prev step through whichever unit
          the user is paginating (vehicles in per-vehicle mode,
          alerts in per-alert mode).  The label flips so "Page 1 of
          2 of 80 vehicles" doesn't get confused with "Page 1 of 22
          of 2164 alerts". */}
      {data && data.count > 0 && (
        <div className="flex items-center justify-between mt-3">
          <p className="text-xs text-muted-foreground">
            {(() => {
              const total = data.count;
              const ps = data.page_size ?? pageSize;
              const cur = data.page ?? page;
              const start = total === 0 ? 0 : (cur - 1) * ps + 1;
              const end = Math.min(cur * ps, total);
              const unit = vehiclesData ? 'vehicles' : 'alerts';
              return (
                <>Showing <strong>{start}</strong>–<strong>{end}</strong> of <strong>{total}</strong> {unit}</>
              );
            })()}
          </p>
          {(data.total_pages ?? 1) > 1 && (
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={(data.page ?? page) <= 1 || isFetching}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-border text-xs font-medium text-foreground/80 hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed transition"
              >
                <ChevronLeft size={14} />
                Prev
              </button>
              <span className="text-xs text-muted-foreground tabular-nums">
                Page <strong>{data.page ?? page}</strong> of <strong>{data.total_pages ?? 1}</strong>
              </span>
              <button
                onClick={() => setPage((p) => Math.min(data.total_pages ?? p, p + 1))}
                disabled={(data.page ?? page) >= (data.total_pages ?? 1) || isFetching}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-border text-xs font-medium text-foreground/80 hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed transition"
              >
                Next
                <ChevronRight size={14} />
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
