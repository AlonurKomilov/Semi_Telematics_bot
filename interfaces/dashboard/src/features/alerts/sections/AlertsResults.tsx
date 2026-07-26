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
 * Selection is DataGrid's `bulkSelection` (checkbox column + top
 * bulk-action bar), passed as a CONTROLLED selection so it still lives
 * in AlertsSelectionContext — shared with LiveAckPanel's sound cue,
 * AlertsBulkError, and the filter-chip clear.  ``isRowSelectable``
 * limits checkboxes to un-acknowledged alerts; "Acknowledge" is the
 * one bulk action.
 *
 * Persona-agnostic — same component for every persona.  Persona-
 * specific summary cards live in dedicated sections (LiveAckPanel,
 * SafetySummaryStrip, VehicleHealthSummary).
 */
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Bell, CheckCircle2 } from 'lucide-react';
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../../components/shell';
import { useQueryClient } from '@tanstack/react-query';
import { apiJSON } from '../../../api/client';
import DataGrid, { type BulkAction } from '../../../components/datagrid';
import type {
  Alert,
  AlertsResponse,
  AnyColumn,
  BulkAckResponse,
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

// The API caps the window at 90 days (days: ge=1, le=90), so this is the
// widest honest "have I really seen everything" check we can offer.
const MAX_WINDOW_DAYS = 90;

export default function AlertsResults() {
  const { t } = useTranslation();
  const tz = useTimezone();
  const qc = useQueryClient();
  const { ackState, narrowed, resetToDefaults, days, setDays } = useAlertsFilters();
  // DataGrid owns the checkbox column + the bulk-action bar now, but
  // the SELECTION still lives in the shared context (LiveAckPanel's
  // sound cue, AlertsBulkError, and the filter-chip clear all read it),
  // so it's passed to DataGrid as a CONTROLLED selection.
  const {
    selected, setSelected, openDrillIn, setAcking, setBulkError,
  } = useAlertsSelection();
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

  // Selection ↔ DataGrid bridge.  DataGrid keys rows by ``id`` string
  // (getRowId), so the context set is projected to strings in, and
  // written back as strings out.  ``acking`` is toggled around the POST
  // so LiveAckPanel's success cue still fires on true→false.
  const selectedStr = useMemo(
    () => new Set(Array.from(selected, String)),
    [selected],
  );

  const ackSelected = async (rows: Record<string, unknown>[]) => {
    setAcking(true);
    setBulkError('');
    try {
      // Backend BulkAckRequest expects int ids — coerce or the
      // validator 422s (Alert.id is widened to string|number).
      const ids = rows.map((r) => Number((r as unknown as Alert).id));
      await apiJSON<BulkAckResponse>('/alerts/bulk-ack', {
        method: 'POST',
        body: { ids },
      });
      await qc.invalidateQueries({ queryKey: ['alerts'] });
      // Clear the selection as the LAST synchronous statement before
      // the finally's setAcking(false) — with NO await between them,
      // React 18 batches both into one commit, so LiveAckPanel's
      // detector sees "selection 0 AND acking true→false" in the same
      // render and fires its sound cue.  (An await here would split
      // them across commits and silently kill the cue.)  DataGrid also
      // clears post-onRun — a no-op once this has run.
      setSelected(new Set());
    } catch (e) {
      setBulkError(e instanceof Error ? e.message : 'Bulk acknowledge failed');
      throw e; // keep the selection so the user can retry
    } finally {
      setAcking(false);
    }
  };

  const bulkActions: BulkAction[] = [
    { label: t('alerts.acknowledge', { defaultValue: 'Acknowledge' }),
      icon: CheckCircle2, onRun: ackSelected },
  ];

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
        // No column filter: Severity has an authoritative SERVER control in
        // the filter bar.  A client-side twin would filter only the loaded
        // batch and silently disagree with it once the window overflows one
        // fetch — two controls per dimension, one of them quietly wrong.
        render: (v) => <SeverityDot severity={v as string} />,
      },
      // Vehicle: sortable, but filtered from the server search above (see
      // the Severity note).
      { key: 'vehicle_name', label: 'Vehicle', sortable: true },
      {
        key: 'alert_type', label: 'Type', sortable: true,
        // Filtered from the server Type chips above (see the Severity note).
        render: (v, row) => {
          const a = row as unknown as Alert;
          return (
            <span className="inline-flex items-center">
              <TypeBadge type={(v as string) || 'unknown'} />
              {/* Occurrence-count badge — "× 5" when this same logical
                  alert has fired multiple times without being cleared. */}
              {(a.occurrence_count ?? 1) > 1 && (
                <span
                  className={`ml-2 inline-block px-2 py-0.5 rounded-md text-xs font-bold ${toneClasses('warn')}`}
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
        // Filtered from the server date window (the range picker above);
        // a client date filter over the loaded batch would contradict it.
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
    // "All caught up" is a claim about the FLEET, so it may only be made
    // when nothing is narrowing the view.  With a type / severity /
    // vehicle filter active the honest statement is "nothing matches
    // these filters" — saying every alert is acknowledged while thousands
    // are pending is a false all-clear, which in a safety product is the
    // one lie we can't ship.
    const clearFilters = (
      <button
        onClick={resetToDefaults}
        className="h-8 px-3 inline-flex items-center rounded-md bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/80 transition-colors"
      >
        Clear all filters
      </button>
    );
    if (narrowed) {
      return (
        <EmptyState
          icon={Bell}
          title="No alerts match these filters"
          description="Other alerts may still be pending — clear the filters to see everything in this window."
          action={clearFilters}
        />
      );
    }
    // The date window narrows "active" too: the query filters on
    // first_seen, which is set once and NEVER bumped on a re-fire — so a
    // chronic alert that has been firing for months sits OUTSIDE a 30-day
    // window while still being unacknowledged.  That makes the
    // longest-running problems the easiest ones to hide, so the all-clear
    // is scoped to the window and offers to widen it.
    const widen = days < MAX_WINDOW_DAYS ? (
      <button
        onClick={() => setDays(MAX_WINDOW_DAYS)}
        className="h-8 px-3 inline-flex items-center rounded-md bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/80 transition-colors"
      >
        Check the last {MAX_WINDOW_DAYS} days
      </button>
    ) : undefined;
    return (
      <EmptyState
        icon={Bell}
        title={
          ackState === 'active'
            ? 'No pending alerts in this window'
            : ackState === 'acknowledged'
              ? 'No acknowledged alerts in this window'
              : 'No alerts in this window'
        }
        description={
          ackState === 'active'
            ? `Every alert that started in the last ${days} days has been acknowledged. Older alerts can still be open — widen the window to check.`
            : 'Try widening the date range.'
        }
        action={widen}
      />
    );
  }

  return (
    <div>
      {/* Truncation notice — the window exceeded the single-fetch cap,
          so client-side filters/groups only see the loaded slice.  The
          server pager (AlertsPagination below the table) steps through
          the overflow. */}
      {totalCount > alerts.length && (
        /* The numbers + batch navigation live in ONE place (the pager
           below); this says the part nothing else does — that search,
           filters and sorting only see the loaded batch. */
        <p className="mb-2 text-xs text-muted-foreground">
          Search, filters and sorting apply to the loaded batch — narrow the
          date window to bring everything into one batch.
        </p>
      )}
      <DataGrid
        // One table, one set of per-user prefs.  Grouping is the
        // operator's choice via any column's ⋮ "Group rows by this" (it
        // shows as a removable "Grouped by …" chip), which replaced the
        // old Per-vehicle / Per-alert toggle — that was a hardcoded
        // special case of this, offering only Vehicle.
        tableId="alerts"
        // savedTabs is deliberately NOT enabled yet.  Saved tabs capture the
        // grid's COLUMN filters, and this grid intentionally has none: every
        // dimension (status / type / severity / vehicle / date) is filtered
        // server-side, because a client filter would silently scope to the
        // loaded batch and disagree with the real total.  Turning tabs on
        // today would give operators a picker with nothing in it.  They
        // become genuinely useful in the same step that moves the filter bar
        // INTO the grid (column filters writing the server query) — see the
        // note in AlertsFilterChips.
        columns={columns}
        data={alerts as unknown as Record<string, unknown>[]}
        // Location only.  Vehicle already has an authoritative SERVER
        // search in the filter bar; a second client-side vehicle search
        // over the loaded batch would silently disagree with it whenever
        // the window overflows one fetch.  Location has no server
        // equivalent, so this is the one place it's searchable — scoped to
        // the loaded batch, which the notice above the table states.
        searchKey={['location']}
        searchPlaceholder="Search location in this batch…"
        // Bulk selection is DataGrid's (checkbox column + top bar);
        // CONTROLLED so the shared context stays the owner.  Only
        // un-acknowledged alerts are selectable, and Acknowledge is the
        // one bulk action.
        bulkSelection
        selectedIds={selectedStr}
        onSelectedIdsChange={(next) => setSelected(next as Set<string | number>)}
        isRowSelectable={(row) => isAckable(row as unknown as Alert)}
        bulkRowLabel={(row) => `alert ${(row as unknown as Alert).id}`}
        bulkActions={bulkActions}
        // Rich group header — the group's value + severity tallies +
        // latest activity, replacing the default "<value> (N)".  Written
        // against `value`, not "vehicle", so it reads correctly whichever
        // column the operator groups by.
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
                <span className={`px-1.5 py-0.5 rounded-md border text-2xs font-medium ${toneClasses('danger')}`}>
                  {counts.critical} critical
                </span>
              )}
              {counts.warning > 0 && (
                <span className={`px-1.5 py-0.5 rounded-md border text-2xs font-medium ${toneClasses('warn')}`}>
                  {counts.warning} warning
                </span>
              )}
              {counts.info > 0 && (
                <span className={`px-1.5 py-0.5 rounded-md border text-2xs font-medium ${toneClasses('info')}`}>
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
