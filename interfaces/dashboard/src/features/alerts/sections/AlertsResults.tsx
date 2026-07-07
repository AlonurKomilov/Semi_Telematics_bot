/**
 * Alerts results section — the main queue UI.
 *
 * Renders one of four mutually-exclusive states based on the query
 * result:
 *
 *   1. error    — query failed and there's no cached data
 *   2. loading  — query is in-flight and there's no cached data
 *   3. empty    — query returned zero rows
 *   4. table    — the shared DataGrid (single source of truth)
 *
 * Both view modes are the SAME DataGrid over the same flat alert
 * rows: "by vehicle" is row-grouping on vehicle_name (pre-set via
 * ``defaultRowGroup``), "list" is the ungrouped flat table.  The
 * query loads the whole filter window (up to the server's 2000-row
 * cap) so client-side filter / sort / group / paginate are honest;
 * a truncation notice + the server pager cover the overflow case.
 *
 * Selection state lives in AlertsSelectionContext (shared with the
 * bulk-ack toolbar) — DataGrid just renders the checkboxes via its
 * ``firstColumnLeading`` hooks: per-row, header select-all, and
 * group-level (per-vehicle) with indeterminate states.
 *
 * Persona-agnostic — same component for every persona.  Persona-
 * specific summary cards live in dedicated sections (LiveAckPanel,
 * SafetySummaryStrip, VehicleHealthSummary).
 */
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Bell } from 'lucide-react';
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../../components/shell';
import DataGrid from '../../../components/DataGrid';
import type {
  Alert,
  AlertsResponse,
  AnyColumn,
  VehiclesAlertsResponse,
} from '../../../types';
import { formatAlertDescription } from '../../../utils/alertDescription';
import { formatDate } from '../../../utils/datetime';
import { useTimezone } from '../../../hooks/useTimezone';
import { toneClasses } from '../../../lib/status';
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

const SEV_RANK: Record<string, number> = { critical: 0, warning: 1, info: 2 };

export default function AlertsResults() {
  const { t } = useTranslation();
  const tz = useTimezone();
  const { ackState, viewMode } = useAlertsFilters();
  const { selected, setSelected, openDrillIn } = useAlertsSelection();
  const { data, isLoading, error: queryError, refetch } = useAlertsQuery();

  // Discriminate the response shape by what the payload actually
  // contains — react-query hands back the previous response as
  // ``placeholderData`` while a filter change is mid-flight, and a
  // stale by-vehicle payload (from before the flat-fetch migration)
  // could still be cached.
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
  const totalCount = data?.count ?? alerts.length;

  const toggleSelect = (id: string | number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Column set — conditional Status column only when the view can
  // contain acknowledged rows ('active' rows are by definition
  // un-acked, so the column would be all-blank noise there).
  const columns = useMemo<AnyColumn[]>(() => {
    const cols: AnyColumn[] = [
      {
        key: 'id', label: 'Alert', sortable: true,
        render: (v, row) => (
          // Clicking the alert-id opens the IncidentDrillInDrawer
          // when one is mounted in the active persona's layout.
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              openDrillIn(row as unknown as Alert);
            }}
            className="font-mono text-xs text-muted-foreground hover:text-primary hover:underline focus:outline-none focus:text-primary"
            title="Open incident details"
          >
            #{String(v)}
          </button>
        ),
      },
      {
        key: 'severity', label: 'Severity', sortable: true,
        // Rank-based sort so critical outranks warning outranks info
        // (alphabetical would bury critical in the middle).
        sortKey: (row) => SEV_RANK[String((row as Alert).severity ?? 'warning')] ?? 3,
        filterable: true,
        filterValue: (row) => String((row as Alert).severity ?? 'warning'),
        filterLabel: (row) => {
          const s = String((row as Alert).severity ?? 'warning');
          return s.charAt(0).toUpperCase() + s.slice(1);
        },
        render: (v) => <SeverityDot severity={v as string} />,
      },
      { key: 'vehicle_name', label: 'Vehicle', sortable: true, filterable: true },
      {
        key: 'alert_type', label: 'Type', sortable: true,
        filterable: true,
        filterValue: (row) => String((row as Alert).alert_type ?? 'unknown'),
        filterLabel: (row) => {
          const s = String((row as Alert).alert_type ?? 'unknown');
          return s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        },
        render: (v, row) => {
          const a = row as unknown as Alert;
          return (
            <span className="inline-flex items-center">
              <TypeBadge type={(v as string) || 'unknown'} />
              {/* Occurrence-count badge — "× 5" when this same logical
                  alert has fired multiple times without being cleared. */}
              {(a.occurrence_count ?? 1) > 1 && (
                <span
                  className={`ml-2 inline-block px-2 py-0.5 rounded-full text-xs font-bold ${toneClasses('warn')}`}
                  title={t('alerts.total_occurrences')}
                >
                  × {a.occurrence_count}
                </span>
              )}
            </span>
          );
        },
      },
      {
        key: 'message', label: 'Description', sortable: false,
        render: (_v, row) => {
          const a = row as unknown as Alert & { last_detail?: string };
          return (
            <span
              className="text-muted-foreground"
              title={a.last_detail || a.message || ''}
            >
              {truncate(formatAlertDescription(a), 80)}
            </span>
          );
        },
      },
      {
        key: 'location', label: 'Location', sortable: false,
        render: (v) => {
          const s = String(v ?? '');
          return s
            ? <span className="text-muted-foreground" title={s}>{truncate(s, 30)}</span>
            : <span className="text-muted-foreground">—</span>;
        },
      },
      {
        key: 'last_seen', label: 'Time', sortable: true,
        sortKey: (row) => {
          const a = row as Alert;
          return a.last_seen || a.created_at || '';
        },
        filterable: true,
        filterMode: 'date-range',
        render: (_v, row) => {
          const a = row as unknown as Alert;
          const iso = a.last_seen || a.created_at;
          return (
            <span className="text-muted-foreground">
              {iso ? formatDate(iso, { timeZone: tz }) : '—'}
            </span>
          );
        },
      },
    ];
    if (ackState !== 'active') {
      cols.push({
        key: 'acknowledged_at', label: 'Status', sortable: true,
        render: (_v, row) => <AckMarker alert={row as unknown as Alert} tz={tz} />,
      });
    }
    return cols;
  }, [ackState, tz, t, openDrillIn]);

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

  const byVehicle = viewMode === 'by-vehicle';

  return (
    <div>
      {/* Truncation notice — the window exceeded the single-fetch cap,
          so client-side filters/groups only see the loaded slice.  The
          server pager (AlertsPagination below the table) steps through
          the overflow. */}
      {totalCount > alerts.length && (
        <p className="mb-2 text-xs text-muted-foreground">
          Showing the latest {alerts.length.toLocaleString()} of{' '}
          {totalCount.toLocaleString()} alerts — narrow the date window or
          filters, or page through below.
        </p>
      )}
      <DataGrid
        // Distinct tableIds per view mode: the two modes persist
        // separate layout / grouping prefs, and the mode toggle in
        // the control bar swaps between them instantly (same data,
        // no refetch).
        tableId={byVehicle ? 'alerts-by-vehicle' : 'alerts-list'}
        defaultRowGroup={byVehicle ? 'vehicle_name' : undefined}
        columns={columns}
        data={alerts as unknown as Record<string, unknown>[]}
        searchKey={['vehicle_name', 'location']}
        searchPlaceholder="Search vehicle or location…"
        // Ack-selection checkboxes ride the first visible column and
        // the row-group headers; state lives in AlertsSelectionContext
        // so the bulk-ack toolbar sees the same set.
        firstColumnLeading={{
          header: () => {
            const ackable = alerts.filter(isAckable);
            const selCount = ackable.filter(a => selected.has(a.id)).length;
            const all = ackable.length > 0 && selCount === ackable.length;
            return (
              <input
                type="checkbox"
                checked={all}
                disabled={ackable.length === 0}
                ref={el => { if (el) el.indeterminate = selCount > 0 && !all; }}
                onClick={e => e.stopPropagation()}
                onChange={() => {
                  setSelected(all ? new Set() : new Set(ackable.map(a => a.id)));
                }}
                className="cursor-pointer accent-primary disabled:cursor-not-allowed"
                aria-label="Select all un-acknowledged alerts"
              />
            );
          },
          cell: (row) => {
            const a = row as unknown as Alert;
            const ackable = isAckable(a);
            return (
              <input
                type="checkbox"
                checked={selected.has(a.id)}
                disabled={!ackable}
                onClick={e => e.stopPropagation()}
                onChange={() => toggleSelect(a.id)}
                title={ackable ? undefined : 'Already acknowledged'}
                className="cursor-pointer accent-primary disabled:cursor-not-allowed disabled:opacity-40"
                aria-label={`Select alert ${a.id}`}
              />
            );
          },
          groupHeader: (_value, rows) => {
            const ackable = (rows as unknown as Alert[]).filter(isAckable);
            if (ackable.length === 0) return null;
            const selCount = ackable.filter(a => selected.has(a.id)).length;
            const all = selCount === ackable.length;
            return (
              <input
                type="checkbox"
                checked={all}
                ref={el => { if (el) el.indeterminate = selCount > 0 && !all; }}
                onChange={() => {
                  setSelected(prev => {
                    const next = new Set(prev);
                    for (const a of ackable) {
                      if (all) next.delete(a.id);
                      else next.add(a.id);
                    }
                    return next;
                  });
                }}
                className="cursor-pointer accent-primary"
                aria-label="Select all un-acknowledged alerts on this vehicle"
              />
            );
          },
        }}
        // Rich group header — vehicle name + severity tallies +
        // latest activity, replacing the default "<value> (N)".
        rowGroupHeader={(value, rows) => {
          const as_ = rows as unknown as Alert[];
          const counts = { critical: 0, warning: 0, info: 0 };
          let latest = '';
          for (const a of as_) {
            const s = (a.severity ?? 'warning') as keyof typeof counts;
            counts[s] = (counts[s] ?? 0) + 1;
            const seen = a.last_seen || a.created_at || '';
            if (seen > latest) latest = seen;
          }
          return (
            <span className="inline-flex flex-wrap items-center gap-2">
              <span className="font-medium text-foreground">
                {String(value ?? 'Unknown')}
              </span>
              <span className="text-xs text-muted-foreground">({as_.length})</span>
              {counts.critical > 0 && (
                <span className={`px-1.5 py-0.5 rounded-full border text-2xs font-medium ${toneClasses('danger')}`}>
                  {counts.critical} critical
                </span>
              )}
              {counts.warning > 0 && (
                <span className={`px-1.5 py-0.5 rounded-full border text-2xs font-medium ${toneClasses('warn')}`}>
                  {counts.warning} warning
                </span>
              )}
              {counts.info > 0 && (
                <span className={`px-1.5 py-0.5 rounded-full border text-2xs font-medium ${toneClasses('info')}`}>
                  {counts.info} info
                </span>
              )}
              {latest && (
                <span className="text-xs text-muted-foreground">
                  {formatDate(latest, { timeZone: tz })}
                </span>
              )}
            </span>
          );
        }}
      />
    </div>
  );
}
