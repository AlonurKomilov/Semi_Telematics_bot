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
import DataGrid, { TAB_PREFIX, type BulkAction } from '../../../components/datagrid';
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
import { useAlertsFilters } from '../_shared/useAlertsFilters';
import { useAlertsSelection } from '../_shared/AlertsSelectionContext';
import { useAlertsQuery, buildAlertsFilterParams } from '../_shared/useAlertsQuery';
import { apiFetch } from '../../../api/client';
import { toast } from 'sonner';
import { useAckAlerts } from '../useRecentAlerts';
import { addStagedAcks, removeStagedAcks, useStagedAckIds } from '../stagedAcks';
import { stagedAction } from '../../../components/banners/stagedAction';
import { useAlertSegmentCounts } from '../_shared/useAlertSegmentCounts';
import { toGridFilters, fromGridFilters } from '../_shared/gridFilterAdapters';
import { familyText,
  STORED_ALERT_TYPES,
  typeText,
  AckMarker,
  SeverityDot,
  TypeBadge,
  isAckable,
  truncate,
} from '../_shared/components';
import { Badge } from '@/components/ui/badge';

const SEV_RANK: Record<string, number> = { critical: 0, warning: 1, info: 2 };

// Above this many rows, acknowledging asks BEFORE staging.
//
// Set to a full default page (25), because SELECT-ALL is the accident
// this catches and select-all on the default page is exactly 25.  Moving
// it to 50 — reasoning only about "volume" once the cancel window
// existed — silently let the most likely mistake through: a countdown
// protects against instant regret, not against not-realising the header
// checkbox took the whole page.
//
// Below it the window carries the protection alone, which is right: a
// modal an operator meets all shift becomes a reflex click.
const ACK_CONFIRM_THRESHOLD = 25;

// Declared filter options for the two server-backed column filters.
//
// These are the account-wide vocabularies, not "whatever is on screen".
// A grid derives select options from its loaded rows, which is right only
// when it holds everything — here it holds a capped page, so choosing one
// value would unload the rest and the menu would offer only that value
// back.
//
// Derived from the stored-type vocabulary + the badge nouns, so the
// options can never name a type the database doesn't produce (the
// 'safety_events' false-all-clear class of bug) and a filter option
// always reads exactly like the badge it filters.  Multi-kind
// families (Events, Parking) keep the family word the Feature column
// shows.  Maintenance / Documents / Scorecard write Board rows since
// the delivery-profiles work, so they filter like any other type.
const TYPE_OPTIONS: { value: string; label: string }[] =
  STORED_ALERT_TYPES.map((v) => ({ value: v, label: typeText(v) }));

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
    tab, applyTab, clearTab,
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

  // Export EVERY matching row, from the server.  The grid holds 25, so
  // its own export could only write those — under a filename an operator
  // would reasonably read as the whole result.  Same filter params as the
  // list (one shared builder) so the file always matches the board.
  const exportAllRows = useCallback(async () => {
    const params = buildAlertsFilterParams({
      typeFilter, severityFilter, vehicleSearch, ackState, days, sort, dir,
    });
    try {
      // apiFetch, not a plain link: the session rides an Authorization
      // header, so an <a href> download would arrive unauthenticated.
      const res = await apiFetch(`/alerts/pending/export?${params.toString()}`);
      const blob = await res.blob();
      const disposition = res.headers.get('content-disposition') ?? '';
      const named = /filename="([^"]+)"/.exec(disposition)?.[1] ?? 'alerts.csv';
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = named;
      link.click();
      URL.revokeObjectURL(url);
      // The server names a capped file "-first-N".  Say so here too — a
      // filename is easy to miss, and a partial export that looks whole
      // is the failure this endpoint exists to prevent.
      if (named.includes('-first-')) {
        toast.warning(
          'Export capped — the file holds the first rows only. Narrow the view to export everything.',
        );
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Export failed');
    }
  }, [typeFilter, severityFilter, vehicleSearch, ackState, days, sort, dir]);

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
    markAckCompleted,
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
  //
  // Rows inside an un-committed acknowledge window are HIDDEN: the
  // operator has said they're done with them, so leaving them sitting
  // there invites acknowledging the same alert twice.  They come back if
  // the window is cancelled or the commit fails — the shared store is
  // module-level because it must outlive this section unmounting.
  const stagedIds = useStagedAckIds();
  const rows = useMemo(
    () => alerts
      .filter((a) => !stagedIds.has(String(a.id)))
      .map((a) => ({ ...a, feature: familyText(a.alert_type ?? '') })),
    [alerts, stagedIds],
  );

  // Selection ↔ DataGrid bridge.  DataGrid keys rows by ``id`` string
  // (getRowId), so the context set is projected to strings in, and
  // written back as strings out.  ``acking`` is toggled around the POST
  // so any consumer watching it sees the commit window.
  const selectedStr = useMemo(
    () => new Set(Array.from(selected, String)),
    [selected],
  );

  // Acknowledging is STAGED, not immediate.  It writes an accountability
  // record per alert under this operator's name and there is no
  // un-acknowledge, so the protection has to come BEFORE the write: the
  // request fires when the countdown ends, and Cancel means nothing was
  // ever sent — no reversal, no "acked then un-acked" noise in a trail
  // someone may have to produce in an audit.
  //
  // Same primitive and same store as the bell's "Acknowledge all", so the
  // two surfaces behave identically and a window survives either one
  // unmounting.
  const ackSelected = (rows: Record<string, unknown>[]) => {
    const ids = rows.map((r) => (r as unknown as Alert).id);
    if (!ids.length) return;
    const strIds = ids.map(String);
    const n = ids.length;
    const noun = `${n} alert${n === 1 ? '' : 's'}`;

    setBulkError('');
    addStagedAcks(strIds);            // rows vanish for the window
    // Selection clears now: the operator is done with these rows, and
    // leaving them ticked behind a banner invites a second click.
    setSelected(new Set());

    stagedAction({
      label: `Acknowledging ${noun}`,
      detail: 'Each acknowledgement is recorded under your name.',
      commit: async (hint) => {
        setAcking(true);
        try {
          // The shared helper invalidates BOTH ['alerts'] (board + hero
          // counts) and ['shell','overview-stats'] (bell badge, Overview
          // card), so no surface is left claiming work already done.
          await ackAlerts(ids, hint);
          // Explicit "an ack landed" event for LiveAckPanel's
          // "Last acknowledged" chip.
          markAckCompleted();
        } catch (e) {
          // The staged banner is transient and offers Retry; AlertsBulkError
          // is the PERSISTENT inline surface, mounted in every layout for
          // exactly this.  Feeding only the banner left it permanently
          // blank and the failure easy to scroll past.
          setBulkError(e instanceof Error ? e.message : 'Bulk acknowledge failed');
          throw e;      // let stagedAction show its Retry banner too
        } finally {
          // Committed: the refetch proves it.  Failed: the rows come
          // BACK rather than staying hidden behind a banner offering
          // Retry — a hidden row the operator thinks is handled is the
          // worse of the two failures.
          removeStagedAcks(strIds);
          setAcking(false);
        }
      },
      successMessage: `Acknowledged ${noun}`,
      onCancel: () => removeStagedAcks(strIds),
    });
  };

  const bulkActions: BulkAction[] = [
    {
      label: t('alerts.acknowledge', { defaultValue: 'Acknowledge' }),
      icon: CheckCircle2,
      // Acknowledging is a COMPLIANCE record — it stamps the operator's
      // name on each alert and takes it out of the open queue, and there
      // is no un-acknowledge.  So the scope gets confirmed above a
      // handful: working through a few rows stays friction-free, while
      // "select all, then click" — the accident this guards — is
      // questioned with the number spelled out.
      //
      // Deliberately NOT a prompt on every action: a modal an operator
      // sees twenty times a shift becomes a reflex click, which protects
      // less than no modal at all while feeling like it protects more.
      // NOT "this cannot be undone" any more — that stopped being true
      // the moment the action became staged, and a warning that overstates
      // the stakes is its own kind of dishonesty.
      confirm: (count) => (count >= ACK_CONFIRM_THRESHOLD
        ? `Acknowledge ${count} alerts?\n\n`
          + 'Each is recorded under your name and leaves the open queue. '
          + 'You get a few seconds to cancel before anything is sent.'
        : ''),
      onRun: ackSelected,
    },
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
            className="font-mono text-xs text-muted-foreground hover:text-primary hover:underline focus:outline-none focus:text-primary py-1 -my-1 min-h-tap"
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
        // ALWAYS shows the family (owner decision 2026-07-27).  A
        // same-word-suppression variant was tried — it compared against
        // the static type label, which the kind-aware badges made stale,
        // and a parking-heavy page collapsed the whole column to dashes,
        // reading as broken data.  Slight repetition on single-type
        // families is honest; the cell always matches what sorting and
        // "Group rows by this" use.
        render: (v) => (
          <span className="text-muted-foreground">{String(v ?? '')}</span>
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
                  <Badge tone="warn" className="ml-2 inline-block font-bold">
                    × {a.occurrence_count}
                  </Badge>
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
        className="h-8 px-3 inline-flex items-center rounded-md bg-primary text-primary-foreground text-xs font-medium hover:bg-primary-hover transition-colors"
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
        className="h-8 px-3 inline-flex items-center rounded-md bg-primary text-primary-foreground text-xs font-medium hover:bg-primary-hover transition-colors"
      >
        Check the last {MAX_WINDOW_DAYS} days
      </button>
    ) : undefined;
    return (
      <EmptyState
        icon={Bell}
        title={
          ackState === 'active'
            ? 'No open alerts'
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
          // A failed REFETCH keeps these rows up, so the
          // ``length === 0`` guard above never fires — the operator
          // reads a stale table as current.  The band says so without
          // removing the rows or the controls.
          error={fetchError || undefined}
          onRetry={() => { void refetch(); }}
        // One table, one set of per-user prefs.  Grouping is the
        // operator's choice via any column's ⋮ "Group rows by this" (it
        // shows as a removable "Grouped by …" chip), which replaced the
        // old Per-vehicle / Per-alert toggle — that was a hardcoded
        // special case of this, offering only Vehicle.
        tableId="alerts"
        // Personal saved tabs.  A tab captures the column filters, and
        // those write the SERVER query, so a tab scopes the whole queue
        // rather than the page that happened to be loaded.
        savedTabs
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
        onExportAllRows={exportAllRows}
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
        // ONE slot, two kinds of occupant: a lifecycle tab or a saved one.
        // The URL records which, so a saved tab survives a refresh and can
        // be shared like any other view.
        segmentKey={tab ? TAB_PREFIX + tab : ackState}
        onSegmentChange={(key, savedTab) => {
          clearSelection();
          if (savedTab) {
            // A saved tab IS its filters — applied in one write so the
            // board makes a single query and the address bar never shows
            // a half-applied scope.  ``baseSegment`` restores the
            // lifecycle slice it was saved under; without it a tab saved
            // among the un-acknowledged would silently widen to all.
            const { typeFilter: type, severityFilter: sev } =
              fromGridFilters(savedTab.filters);
            const captured = savedTab.sort?.[0];
            applyTab(key.slice(TAB_PREFIX.length), {
              typeFilter: type,
              severityFilter: sev,
              vehicleSearch: savedTab.search,
              ackState: savedTab.baseSegment as typeof ackState | undefined,
              sort: captured?.id,
              dir: captured?.desc === false ? 'asc' : 'desc',
            });
            return;
          }
          // No clearTab() here: setAckState writes through setParam, which
          // drops `tab` already.  Calling both pushed TWO history entries
          // for one click, so Back landed on a half-changed URL nobody
          // asked for.
          setAckState(key as typeof ackState);
        }}
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
                <Badge tone="danger">
                  {counts.critical} critical
                </Badge>
              )}
              {counts.warning > 0 && (
                <Badge tone="warn">
                  {counts.warning} warning
                </Badge>
              )}
              {counts.info > 0 && (
                <Badge tone="info">
                  {counts.info} info
                </Badge>
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
