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
 * Every narrowing runs on the SERVER — filters, search, Status and now
 * sort and pagination.  The grid fetches ONE page (25 by default) and
 * renders it; it never holds the queue.  That's what lets a 3,984-row
 * board sort correctly and page with real page numbers instead of
 * stepping through 2,000-row batches.
 *
 * What is still LOCAL is row-grouping and pivot, which need the whole
 * set to mean anything — ``totalRows`` tells the grid it holds a page so
 * it disables those with a reason rather than grouping 25 rows and
 * presenting it as the answer.
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
import { useMemo, useCallback, useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Bell, CheckCircle2 } from 'lucide-react';
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../../components/shell';
import DataGrid, { type BulkAction } from '../../../components/datagrid';
import type { ColumnFiltersState } from '@tanstack/react-table';
import { Tip } from '../../../components/tooltip';
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
import { useAckAlerts } from '../useRecentAlerts';
import { useAlertSegmentCounts } from '../_shared/useAlertSegmentCounts';
import { toGridFilters, fromGridFilters } from '../_shared/gridFilterAdapters';
import { familyText,
  AckMarker,
  SeverityDot,
  TypeBadge,
  isAckable,
  truncate,
} from '../_shared/components';

const SEV_RANK: Record<string, number> = { critical: 0, warning: 1, info: 2 };

// Declared filter options for the two server-backed column filters.
//
// These are the account-wide vocabularies, not "whatever is on screen".
// A grid derives select options from its loaded rows, which is right only
// when it holds everything — here it holds a capped page, so choosing one
// value would unload the rest and the menu would offer only that value
// back.  Labels are Title case to match the badges in the cells.
const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'fault', label: 'Fault' },
  { value: 'health', label: 'Health' },
  { value: 'fuel', label: 'Fuel' },
  { value: 'events', label: 'Events' },
  { value: 'parking', label: 'Parking' },
  // No "Maintenance Due": those alerts route through the lite forum path
  // and are never written to alert_history, so the filter would always
  // return an empty board.  Offering a control that can only disappoint
  // is worse than not offering it.  (Same reason VehicleHealthSummary has
  // no maintenance card.)
];

const SEVERITY_OPTIONS: { value: string; label: string }[] = [
  { value: 'critical', label: 'Critical' },
  { value: 'warning', label: 'Warning' },
  { value: 'info', label: 'Info' },
];

// Status is a LIFECYCLE dimension, so it's grid tabs rather than a column
// filter — and it changes what the server returns (an acknowledged row
// isn't in the response at all while viewing the open queue), which is
// why it can't be a column filter even in principle.
const ACK_SEGMENTS = [
  { key: 'active', label: 'Not acknowledged' },
  { key: 'acknowledged', label: 'Acknowledged' },
  { key: 'all', label: 'All' },
];

// The API caps the window at 90 days (days: ge=1, le=90), so this is the
// widest honest "have I really seen everything" check we can offer.
const MAX_WINDOW_DAYS = 90;

export default function AlertsResults() {
  const { t } = useTranslation();
  const tz = useTimezone();
  const {
    ackState, setAckState, narrowed, resetToDefaults, days, setDays,
    typeFilter, setTypeFilter, severityFilter, setSeverityFilter,
    vehicleSearch, setVehicleSearch,
    page, setPage, pageSize, setPageSize, sort, dir, setSort,
  } = useAlertsFilters();
  const { counts: segmentCounts } = useAlertSegmentCounts();

  // ── The grid's view-state IS the page's URL state ────────────────
  //
  // Type / Severity / search / Status all narrow on the SERVER, because
  // the board holds a capped page of a much larger queue: a client-side
  // narrowing would filter the loaded rows and report that as the answer
  // for all of them.  So the grid renders the controls and reports
  // intent, and these adapters turn that intent into the query.
  //
  // ``'all'`` is the absence of a filter, which is why it's a sentinel in
  // the URL rather than a value in the option lists.
  const gridFilters = useMemo(
    () => toGridFilters(typeFilter, severityFilter),
    [typeFilter, severityFilter],
  );

  // The search box types into a DRAFT and lands in the URL on a pause.
  // Every keystroke otherwise fires two server round-trips (the row
  // list and the tab counts, which share this filter), and an unsettled
  // URL also spams the history.  The draft follows the URL when it
  // changes from elsewhere — "clear all filters", a shared link.
  const [searchDraft, setSearchDraft] = useState(vehicleSearch);
  useEffect(() => { setSearchDraft(vehicleSearch); }, [vehicleSearch]);
  useEffect(() => {
    if (searchDraft === vehicleSearch) return;
    const id = setTimeout(() => setVehicleSearch(searchDraft), 300);
    return () => clearTimeout(id);
  }, [searchDraft, vehicleSearch, setVehicleSearch]);

  const onGridFiltersChange = useCallback((next: ColumnFiltersState) => {
    const { typeFilter: type, severityFilter: sev } = fromGridFilters(next);
    setTypeFilter(type);
    setSeverityFilter(sev);
  }, [setTypeFilter, setSeverityFilter]);
  // DataGrid owns the checkbox column + the bulk-action bar now, but
  // the SELECTION still lives in the shared context (LiveAckPanel's
  // sound cue, AlertsBulkError, and the filter-chip clear all read it),
  // so it's passed to DataGrid as a CONTROLLED selection.
  const {
    selected, setSelected, clearSelection, openDrillIn, setAcking, setBulkError,
  } = useAlertsSelection();
  const { data, isLoading, error: queryError, refetch } = useAlertsQuery();
  const ackAlerts = useAckAlerts();

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

  // Rows carry the derived feature family so the Feature column's
  // sort / row-grouping read a real field like any other column.
  const rows = useMemo(
    () => alerts.map((a) => ({ ...a, feature: familyText(a.alert_type ?? '') })),
    [alerts],
  );

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
      // The shared helper POSTs and invalidates BOTH ['alerts'] (board +
      // hero counts) and ['shell','overview-stats'] (the bell badge and
      // the Overview card).  Acking here used to refresh only the first,
      // leaving the badge claiming work that was already done.
      await ackAlerts(rows.map((r) => (r as unknown as Alert).id));
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
          // Keyboard path to the drawer.  The whole row is clickable
          // (onRowClick below), but a row handler is mouse-only, so the
          // id stays a real focusable button.
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              openDrillIn(row as unknown as Alert);
            }}
            className="font-mono text-xs text-muted-foreground hover:text-primary hover:underline focus:outline-none focus:text-primary"
            // The row itself is clickable now, so this needs no hover
            // label — it needs a NAME, for the keyboard and screen-reader
            // path where the row's own handler isn't reachable.
            aria-label={t('alerts.drillin.open_alert_details')}
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
        // Filters the SERVER query (see the DataGrid props below), so it
        // narrows the whole queue rather than the loaded page.  Options are
        // declared, not derived: derivation reads the rows in hand, so
        // picking "Critical" would unload every other severity and leave
        // the menu offering only the value already chosen.
        filterable: true,
        filterMode: 'select',
        filterOptions: SEVERITY_OPTIONS,
        render: (v) => <SeverityDot severity={v as string} />,
      },
      // Vehicle: sortable; narrowing is the grid's search box, which runs
      // server-side over vehicle name AND location.
      { key: 'vehicle_name', label: 'Vehicle', sortable: true },
      {
        // Owning FEATURE family — answers "Fuel belongs to Vehicle,
        // Events to Safety" so the Type column stops reading like a
        // list of standalone features.  The value is stamped onto each
        // row (see ``rows`` below), so sorting and ⋮ "Group rows by
        // this" work through the plain column path.  Deliberately NOT
        // client-filterable — this grid filters server-side only (see
        // the DataGrid props below); to narrow by family, pick its types
        // in the Type column filter.
        key: 'feature', label: 'Feature', sortable: true,
        render: (v) => (
          <span className="text-muted-foreground">{v as string}</span>
        ),
      },
      {
        key: 'alert_type', label: 'Type', sortable: true,
        // Server-backed, declared options — same reasoning as Severity.
        filterable: true,
        filterMode: 'select',
        filterOptions: TYPE_OPTIONS,
        render: (v, row) => {
          const a = row as unknown as Alert;
          return (
            <span className="inline-flex items-center">
              <TypeBadge type={(v as string) || 'unknown'} kind={a.kind} />
              {/* Occurrence-count badge — "× 5" when this same logical
                  alert has fired multiple times without being cleared. */}
              {(a.occurrence_count ?? 1) > 1 && (
                <Tip label={t('alerts.total_occurrences')}>
                  <span
                    className={`ml-2 inline-block px-2 py-0.5 rounded-md text-xs font-bold ${toneClasses('warn')}`}
                  >
                    × {a.occurrence_count}
                  </span>
                </Tip>
              )}
            </span>
          );
        },
      },
      {
        key: 'message', label: 'Description', sortable: false,
        render: (_v, row) => {
          const a = row as unknown as Alert;
          const text = formatAlertDescription(a);
          // The description is the most task-relevant cell in the row, so
          // it reads at normal weight — it used to be the FAINTEST thing
          // on screen while the id and severity dominated.
          //
          // No hover text: this used to expose ``last_detail`` raw
          // ("parking:unsafe:8h", "crash:281474998895725-1782771648515"),
          // which is a machine key, not copy.  The full sentence lives in
          // the details drawer, which the row now opens.
          return (
            <span className="text-foreground">
              {truncate(text, 80)}
            </span>
          );
        },
      },
      {
        key: 'location', label: 'Location', sortable: false,
        render: (v) => {
          const s = String(v ?? '');
          // Truncated at 30 chars, so the full address needs to stay
          // reachable — via the tooltip primitive, not a native title
          // (unthemed, and invisible on touch).
          return s
            ? (
              <Tip label={s}>
                <span className="text-muted-foreground">{truncate(s, 30)}</span>
              </Tip>
            )
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
          description={
            // "this window" would be a lie on the open queue, which is
            // unwindowed — only the acknowledged / all views are bounded
            // by the date range.
            ackState === 'active'
              ? 'Other alerts may still be pending — clear the filters to see the whole open queue.'
              : 'Other alerts may still be pending — clear the filters to see everything in this window.'
          }
          action={clearFilters}
        />
      );
    }
    // The open queue is UNWINDOWED (see _alert_filter_clause), so an empty
    // active view genuinely means nothing is open — the all-clear can be
    // stated without a date caveat again.  The acknowledged / all views ARE
    // windowed, so they say so and offer to widen.
    const widen = ackState !== 'active' && days < MAX_WINDOW_DAYS ? (
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
            ? 'No pending alerts'
            : ackState === 'acknowledged'
              ? 'No acknowledged alerts in this window'
              : 'No alerts in this window'
        }
        description={
          ackState === 'active'
            ? 'Every alert has been acknowledged — including older ones, which the open queue never hides.'
            : 'Try widening the date range.'
        }
        action={widen}
      />
    );
  }

  return (
    <div>
      <DataGrid
        // One table, one set of per-user prefs.  Grouping is the
        // operator's choice via any column's ⋮ "Group rows by this" (it
        // shows as a removable "Grouped by …" chip), which replaced the
        // old Per-vehicle / Per-alert toggle — that was a hardcoded
        // special case of this, offering only Vehicle.
        tableId="alerts"
        // savedTabs stays OFF for one more step.  The filters a tab
        // captures are now server-truthful, which was the blocker — but a
        // tab and an ack-state are both "the active segment", and this
        // page models that slot as ackState alone.  Selecting a tab would
        // hand ``tab:<id>`` to setAckState, which normalises to 'active',
        // so the tab's filters would be dropped and it wouldn't even show
        // as selected.  Turning it on needs a page-side segment key that
        // can hold either kind; DataGrid already passes a tab's captured
        // {filters, search} to onSegmentChange for exactly that.
        columns={columns}
        data={rows as unknown as Record<string, unknown>[]}
        // The WHOLE row opens the details drawer.  Previously only the
        // small grey id was clickable, so clicking the vehicle, the
        // description or the time — the parts an operator actually reads
        // — did nothing.  DataGrid draws the pointer cursor for us; the
        // id stays a real <button> so the row is still keyboard-reachable
        // (a click handler on the row alone is mouse-only).
        onRowClick={(row) => openDrillIn(row as unknown as Alert)}
        // Every narrowing on this board happens on the SERVER — see the
        // adapters at the top of this component.  The grid renders the
        // controls and reports intent; it must not also narrow the rows,
        // or it would filter the loaded page and call that the answer.
        manualFiltering
        // The grid holds ONE page of a much bigger queue.  Sorting and
        // paging are the server's now, so they stay correct — but grouping
        // and pivot are still local and would work on 25 rows, which is
        // what totalRows keeps them honest about.
        totalRows={totalCount}
        // Order is decided in SQL, so the grid must not re-sort the page
        // it was handed (that would order 25 rows and read as ordering
        // 3,984).  It reports the click; the query carries it.
        manualSorting
        sorting={sort ? [{ id: sort, desc: dir === 'desc' }] : []}
        onSortingChange={(next) => {
          const first = next[0];
          setSort(first?.id ?? '', first?.desc === false ? 'asc' : 'desc');
        }}
        // Real pages against the real total — no more "Batch 1 of 2".
        manualPagination
        pageIndex={page - 1}
        pageSize={pageSize}
        pageCount={Math.max(1, Math.ceil(totalCount / pageSize))}
        onPaginationChange={({ pageIndex, pageSize: size }) => {
          // Selection is PAGE-SCOPED, because only this page's rows are
          // loaded.  Carrying ids across pages looked like it worked —
          // the bulk bar counted 5 — but Acknowledge resolves ids against
          // the rows in hand, so the 3 from the previous page silently
          // dropped, 2 were acknowledged, and the selection was then
          // cleared as if all 5 had been.  Three live safety alerts,
          // reported as handled.  Clearing here makes the count mean what
          // it says.
          clearSelection();
          if (size !== pageSize) setPageSize(size);
          setPage(pageIndex + 1);
        }}
        columnFilters={gridFilters}
        onColumnFiltersChange={onGridFiltersChange}
        // Status: a lifecycle dimension, and one the server owns — an
        // acknowledged row isn't in the response at all while the open
        // queue is showing, so it could never have been a column filter.
        // Counts come from the server for the same reason the filters do.
        segments={ACK_SEGMENTS}
        segmentKey={ackState}
        onSegmentChange={(key) => setAckState(key as typeof ackState)}
        segmentCounts={segmentCounts}
        // One search box, searching vehicle name AND location across the
        // whole queue.  It replaces two controls that each told a partial
        // truth: a server vehicle search that ignored location, and a
        // location search scoped to the rows already loaded.
        searchKey={['vehicle_name', 'location']}
        searchPlaceholder="Search vehicle or location…"
        globalFilter={searchDraft}
        onGlobalFilterChange={setSearchDraft}
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
