/**
 * Alerts results section — the main queue UI.
 *
 * Renders one of five mutually-exclusive states based on the query
 * result + ack-state filter:
 *
 *   1. error    — query failed and there's no cached data
 *   2. loading  — query is in-flight and there's no cached data
 *   3. empty    — query returned zero rows
 *   4. by-vehicle — vehicle cards with expandable per-alert tables
 *   5. list     — flat table of alerts
 *
 * Owns the local helpers (toggleSelect, selectAllForVehicle,
 * toggleVehicleExpanded) and the vehicleGroups memo.  Reads filter
 * state via useAlertsFilters, selection via useAlertsSelection,
 * data via useAlertsQuery.
 *
 * Persona-agnostic — same component for every persona.  Persona-
 * specific summary cards live in dedicated sections (LiveAckPanel,
 * SafetySummaryStrip, VehicleHealthSummary).
 */
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Bell, ChevronDown } from 'lucide-react';
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../../components/shell';
import type {
  Alert,
  AlertsResponse,
  VehiclesAlertsResponse,
} from '../../../types';
import { formatAlertDescription } from '../../../utils/alertDescription';
import { statusClasses, toneClasses } from '../../../lib/status';
import { useAlertsFilters } from '../_shared/useAlertsFilters';
import { useAlertsSelection } from '../_shared/AlertsSelectionContext';
import { useAlertsQuery } from '../_shared/useAlertsQuery';
import {
  AckMarker,
  SeverityDot,
  TypeBadge,
  isAckable,
  truncate,
} from '../_shared/components';

export default function AlertsResults() {
  const { t } = useTranslation();
  const { ackState, viewMode } = useAlertsFilters();
  const {
    selected,
    setSelected,
    expandedVehicles,
    setExpandedVehicles,
    openDrillIn,
  } = useAlertsSelection();
  const { data, isLoading, error: queryError, refetch } = useAlertsQuery();

  // Discriminate the response shape by what the payload actually
  // contains, not by viewMode alone — react-query hands back the
  // previous response as ``placeholderData`` while a toggle is
  // mid-flight, so right after the user flips to per-vehicle the
  // ``data`` object can still be the old ``{ alerts: [...] }`` shape.
  // Casting that to a vehicles shape and calling ``.map()`` on the
  // missing ``vehicles`` prop would blow up the page.
  const vehiclesData =
    data && Array.isArray((data as VehiclesAlertsResponse).vehicles)
      ? (data as VehiclesAlertsResponse)
      : undefined;
  const alertsData =
    data && Array.isArray((data as AlertsResponse).alerts)
      ? (data as AlertsResponse)
      : undefined;
  const alerts: Alert[] =
    alertsData?.alerts ??
    (vehiclesData?.vehicles ?? []).flatMap((v) => v.alerts ?? []);

  const fetchError = queryError instanceof Error ? queryError.message : '';

  // Map server-aggregated shape onto the group struct the JSX expects.
  // When we're on the flat /pending fetch (list view) we still derive
  // vehicleGroups client-side off the same alerts array so the
  // expand affordance keeps working — used if the user ever flips to
  // per-vehicle while the previous /pending payload is still hanging
  // around as react-query placeholderData.
  const vehicleGroups = useMemo(() => {
    if (vehiclesData) {
      const sevRank: Record<string, number> = { critical: 0, warning: 1, info: 2 };
      return (vehiclesData.vehicles ?? []).map((v) => {
        const vAlerts = v.alerts ?? [];
        const types = new Set<string>();
        for (const a of vAlerts) if (a.alert_type) types.add(a.alert_type);
        const max_severity =
          (v.critical_count ?? 0) > 0
            ? 'critical'
            : (v.warning_count ?? 0) > 0
              ? 'warning'
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
    const groups = new Map<
      string,
      {
        key: string;
        vehicle_name: string;
        vehicle_id: string;
        alerts: Alert[];
        counts: { critical: number; warning: number; info: number };
        types: Set<string>;
        max_severity: string;
        latest_seen: string;
      }
    >();
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

  function toggleVehicleExpanded(key: string) {
    setExpandedVehicles((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function selectAllForVehicle(
    group: (typeof vehicleGroups)[number],
    on: boolean,
  ) {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const a of group.alerts) {
        if (!isAckable(a)) continue;
        if (on) next.add(a.id);
        else next.delete(a.id);
      }
      return next;
    });
  }

  function toggleSelect(id: string | number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (fetchError && alerts.length === 0) {
    return (
      <ErrorState
        title="Couldn't load alerts"
        message={fetchError}
        onRetry={() => refetch()}
      />
    );
  }

  if (isLoading && alerts.length === 0) {
    return <TableSkeleton rows={6} cols={5} />;
  }

  if (alerts.length === 0) {
    return (
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
            ? "You're all caught up — every alert has been acknowledged."
            : 'Try widening the date range or removing filters.'
        }
      />
    );
  }

  if (viewMode === 'by-vehicle') {
    return (
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
          const selectedOnPage = pageAlertIds.filter((id) =>
            selected.has(id),
          ).length;
          const allOnPageSelected =
            totalOnPage > 0 && selectedOnPage === totalOnPage;
          const someOnPageSelected =
            selectedOnPage > 0 && selectedOnPage < totalOnPage;
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
            const allSelected =
              ackable.length > 0 && ackable.every((a) => selected.has(a.id));
            const someSelected = ackable.some((a) => selected.has(a.id));
            const expanded = expandedVehicles.has(g.key);
            const sevBadgeBase = 'px-2 py-0.5 rounded-full text-xs font-medium';
            return (
              <div key={g.key} className="bg-card">
                <div className="grid grid-cols-[2rem_1.25rem_minmax(8rem,1.4fr)_minmax(10rem,1.6fr)_minmax(14rem,2fr)_minmax(11rem,1fr)] gap-3 items-center px-4 py-3 hover:bg-muted/40 transition">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    disabled={ackable.length === 0}
                    ref={(el) => {
                      // Indeterminate when only part of this vehicle's
                      // ackable alerts are selected — the bulk-ack
                      // will close a subset, not the whole truck.
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
                      <span
                        className={`${sevBadgeBase} ${statusClasses('critical')}`}
                      >
                        {g.counts.critical} critical
                      </span>
                    )}
                    {g.counts.warning > 0 && (
                      <span
                        className={`${sevBadgeBase} ${statusClasses('warning')}`}
                      >
                        {g.counts.warning} warning
                      </span>
                    )}
                    {g.counts.info > 0 && (
                      <span
                        className={`${sevBadgeBase} ${statusClasses('info')}`}
                      >
                        {g.counts.info} info
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground whitespace-nowrap">
                    {g.latest_seen
                      ? new Date(g.latest_seen).toLocaleString()
                      : '—'}
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
                          {ackState !== 'active' && (
                            <th className="px-4 py-2 text-left">Status</th>
                          )}
                        </tr>
                      </thead>
                      <tbody>
                        {g.alerts.map((a) => (
                          <tr
                            key={a.id}
                            className="border-t border-border hover:bg-muted/30"
                          >
                            <td className="px-4 py-2 w-8">
                              <input
                                type="checkbox"
                                checked={selected.has(a.id)}
                                disabled={!isAckable(a)}
                                onChange={() => toggleSelect(a.id)}
                                title={
                                  isAckable(a) ? undefined : 'Already acknowledged'
                                }
                              />
                            </td>
                            <td className="px-4 py-2 text-xs font-mono w-20">
                              {/* Clicking the alert-id opens the
                                  IncidentDrillInDrawer when one is
                                  mounted in the active persona's
                                  layout (safety today).  On personas
                                  whose layout doesn't include the
                                  drawer the click writes context
                                  state but nothing renders it —
                                  visible no-op, no error.  Pointer
                                  + hover styling reflects that this
                                  is a real action target. */}
                              <button
                                type="button"
                                onClick={() => openDrillIn(a)}
                                className="text-muted-foreground hover:text-primary hover:underline focus:outline-none focus:text-primary"
                                title="Open incident details"
                              >
                                #{a.id}
                              </button>
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
                              {a.last_seen
                                ? new Date(a.last_seen).toLocaleString()
                                : '—'}
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
    );
  }

  // viewMode === 'list'
  return (
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
                const selectedCount = ackableIds.filter((id) =>
                  selected.has(id),
                ).length;
                const allSel =
                  ackableIds.length > 0 && selectedCount === ackableIds.length;
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
              <td className="px-4 py-3 text-xs font-mono">
                <button
                  type="button"
                  onClick={() => openDrillIn(a)}
                  className="text-muted-foreground hover:text-primary hover:underline focus:outline-none focus:text-primary"
                  title="Open incident details"
                >
                  #{a.id}
                </button>
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
                    className={`ml-2 inline-block px-2 py-0.5 rounded-full text-xs font-bold ${toneClasses('warn')}`}
                    title={t('alerts.total_occurrences')}
                  >
                    × {a.occurrence_count}
                  </span>
                )}
              </td>
              {/* Description — friendly sentence rendered by the
                  shared formatter so dispatchers don't have to
                  decode ``parking:unsafe:8h`` / ``fuel:19`` / raw
                  event-IDs.  Raw ``last_detail`` stays available on
                  hover for support follow-ups. */}
              <td
                className="px-4 py-3 text-sm text-muted-foreground max-w-xs"
                title={
                  (a as Alert & { last_detail?: string }).last_detail ||
                  (a as Alert & { message?: string }).message ||
                  ''
                }
              >
                {truncate(
                  formatAlertDescription(
                    a as Alert & { last_detail?: string; message?: string },
                  ),
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
                  : a.created_at
                    ? new Date(a.created_at).toLocaleString()
                    : '—'}
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
  );
}
