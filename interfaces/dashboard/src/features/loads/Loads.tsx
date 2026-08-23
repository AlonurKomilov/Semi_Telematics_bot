/**
 * Loads — the canonical load/shipment list (the "All Loads" surface).
 *
 * OUR loads table is the single source of truth: rows are hand-entered
 * (source=manual) or projected in by a connected TMS (source=datatruck).
 * Status tabs mirror the load lifecycle; drivers see only their own loads
 * (scoped server-side), managers see the account's.
 */

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { Clock, Package, Plus } from 'lucide-react';
import DataGrid, { TAB_PREFIX, type DataGridSegment } from '../../components/datagrid';
import { loadRowMenu } from './contextMenu';
import {
  PageHeader, EmptyState, ErrorState, TableSkeleton, DateRangePresets,
} from '../../components/shell';
import { Button } from '../../components/ui/button';
import { useRoleView } from '../../context/RoleViewContext';
import { statusClasses } from '../../lib/status';
import type { AnyColumn } from '../../types';
import LoadManageDialog from './LoadManageDialog';
import LayoverDialog from './LayoverDialog';
import type { PersonOption } from './LayoverDialog';
import { LOAD_STATUSES, listLoads } from './api';
import { InfoTip } from '../../components/tooltip';
import { usePreference } from '../../preferences/usePreference';
import { useTimezone } from '../../hooks/useTimezone';
import { daysAgoInTimeZone } from '../../utils/datetime';
import type { LoadRow, LoadsResponse } from './api';

function Pill({ value }: { value: unknown }) {
  const v = String(value || '');
  if (!v) return <span className="text-muted-foreground">—</span>;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium capitalize ${statusClasses(v)}`}>
      {v.replace('_', '-')}
    </span>
  );
}

// CACHED formatter: this renders once per pivot cell, so a fresh
// Intl instance per call was thousands of allocations per render.
const MONEY = new Intl.NumberFormat(undefined, {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
});

function MoneyCell({ value }: { value: unknown }) {
  if (value == null || value === '') return <span className="text-muted-foreground">—</span>;
  return (
    <span className="tabular-nums font-medium">
      ${MONEY.format(Number(value))}
    </span>
  );
}

function SourceCell({ value }: { value: unknown }) {
  const v = String(value || '');
  if (!v || v === 'manual') return <span className="text-muted-foreground">—</span>;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium capitalize ${statusClasses('connected')}`}>
      {v}
    </span>
  );
}

const makeColumns = (): AnyColumn[] => [
  { key: 'seq', label: 'ID', sortable: true, minWidth: 72, render: (v) => (v ? <span className="font-mono text-xs text-muted-foreground">{`#${v}`}</span> : <span className="text-muted-foreground">—</span>) },
  { key: 'load_number', label: 'Load #', sortable: true, minWidth: 110, render: (v) => (v ? <span className="font-medium">{String(v)}</span> : <span className="text-muted-foreground">—</span>) },
  { key: 'customer', label: 'Customer', sortable: true, filterable: true, pivotable: true, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  { key: 'driver_name', label: 'Driver', sortable: true, filterable: true, pivotable: true, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  { key: 'vehicle_unit', label: 'Truck', sortable: true, minWidth: 88, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  { key: 'trailer_unit', label: 'Trailer', sortable: true, minWidth: 88, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  { key: 'company_code', label: 'Company', sortable: true, filterable: true, pivotable: true, minWidth: 104, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  { key: 'pickup_location', label: 'Pickup', sortable: true, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  { key: 'delivery_location', label: 'Delivery', sortable: true, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  {
    key: 'delivery_date', label: 'DEL date', sortable: true,
    // A date column narrower than this renders "2026-0…", which is
    // not a date.
    minWidth: 116,
    // Date aggregation: earliest / latest delivery across the filtered
    // (or grouped) loads.  aggType 'date' hides sum/avg from the menu
    // and formats the min/max as a day in the account timezone.
    aggregable: true,
    aggType: 'date',
    // As a pivot DIMENSION this offers Year / Quarter / Month, generated
    // from `aggType: 'date'` (see pivot/derived.ts) — no hand-written
    // bucketing here, and the account timezone is applied for us.
    pivotable: true,
    render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>),
  },
  {
    key: 'total_rate', label: 'Rate', sortable: true, minWidth: 104,
    // Aggregable: operators total / average the rate across the filtered
    // loads via the ⋮ menu → footer total.  No ``|| 0`` on aggValue — a
    // load with no rate yet yields NaN, which DataGrid's Number.isFinite
    // filter drops from sum/avg/min/max (so it never counts as a $0 trip
    // in the Average); ``count`` still counts every filtered row.
    aggregable: true,
    // Min alongside Max — offering one without the other reads as an
    // oversight, and "cheapest load" is as real a question as "priciest".
    aggFns: ['sum', 'avg', 'min', 'max', 'count'],
    aggValue: (row) => Number((row as { total_rate?: number | null }).total_rate),
    // Money for the numeric totals; a plain count for ``count`` (else
    // a row count would render as a dollar amount).
    aggFormat: (value, fn) =>
      fn === 'count'
        ? <span className="tabular-nums">{value.toLocaleString()}</span>
        : <MoneyCell value={value} />,
    render: (v, row) => {
      const extra = Number((row as { other_pay?: number | null }).other_pay || 0);
      return (
        <span title={extra > 0 ? `Includes $${extra.toLocaleString()} extra pay (accessorials)` : undefined}>
          <MoneyCell value={v} />
        </span>
      );
    },
  },
  {
    key: 'settlement_ref', label: 'Settled', sortable: true, filterable: true,
    filterValue: (row) => ((row as { settlement_ref?: string }).settlement_ref ? 'settled' : 'unsettled'),
    filterLabel: (row) => ((row as { settlement_ref?: string }).settlement_ref ? 'Settled' : 'Not settled'),
    render: (v, row) => {
      const ref = String(v || '');
      if (!ref) return <span className="text-muted-foreground">—</span>;
      const st = String((row as { settlement_status?: string }).settlement_status || '');
      return (
        <span
          className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium ${statusClasses('completed')}`}
          title={st ? `Settlement ${ref} · ${st}` : `Settlement ${ref}`}
        >
          {ref}
        </span>
      );
    },
  },
  { key: 'status', label: 'Status', sortable: true, minWidth: 104, render: (v) => <Pill value={v} /> },
  { key: 'source', label: 'Source', sortable: true, render: (v) => <SourceCell value={v} /> },
];

// Lifecycle tabs, DECLARED.  No ``match`` on purpose: the SERVER does
// the slicing here (``listLoads(tab)``) and returns authoritative
// per-status counts, so a local predicate would re-filter an already
// filtered page and the tallies would drift.  That is exactly the
// controlled-segments contract — segments + segmentKey +
// onSegmentChange + segmentCounts.
//
// ``canceled`` stays out of the strip deliberately.
// Wider than the shared default (which stops at 90) because this window
// is what reaches HISTORY: the whole point of the control is that a year
// of delivered loads is retrievable.  "All time" is days=0 — the page
// then sends no bound at all and the server returns everything up to its
// cap.
const RANGE_OPTIONS = [
  { label: 'Last 7 days', days: 7 },
  { label: 'Last 30 days', days: 30 },
  { label: 'Last 90 days', days: 90 },
  { label: 'Last 12 months', days: 365 },
  { label: 'All time', days: 0 },
];

const LOAD_SEGMENTS: DataGridSegment[] = [
  // "All" carries no badge: it is never counted server-side, and a 0
  // there would read as "no loads at all".  Every KEYED tab shows its
  // number even when it is 0 — "nothing is upcoming" is an answer, and
  // hiding it made that tab look identical to All.
  { key: 'all', label: 'All', showCount: false },
  ...LOAD_STATUSES.filter((s) => s !== 'canceled').map((s) => ({
    key: s,
    label: s.replace('_', '-').replace(/\b\w/g, (c) => c.toUpperCase()),
  })),
];

export default function Loads() {
  const { t } = useTranslation();
  const columns = useMemo(() => makeColumns(), []);
  const { viewHas } = useRoleView();
  const qc = useQueryClient();
  // ``tab`` is the SERVER slice ('' = all).  ``savedId`` is separate
  // because one strip now holds two kinds of occupant — a lifecycle tab
  // or a personal saved one — and only the first may ever reach the API.
  // Without the split a saved tab's id would be sent as ``?status=`` and
  // the query would return nothing.
  const [tab, setTab] = useState('');
  const [savedId, setSavedId] = useState('');
  const [editing, setEditing] = useState<LoadRow | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [layoverOpen, setLayoverOpen] = useState(false);

  // Gate on the ACTIVE VIEW's permission so role-preview stays honest.
  // Any Manage holder edits ANY load (the own-scope wall came down
  // 2026-07-29) — the load's history trail answers "who changed what".
  const canManage = viewHas('can_manage_loads');
  const canEditLoad = (_row: LoadRow): boolean => canManage;

  // The pickup-date window.  Days back from today, 0 = all time; kept as
  // a per-user preference because it describes how this person works —
  // a dispatcher lives in the last few days, an accountant reconciles a
  // whole month — not how this screen should look for everyone.
  const tz = useTimezone();
  const { value: rangeDays, setValue: setRangeDays } = usePreference('loads.rangeDays');
  const since = rangeDays > 0 ? daysAgoInTimeZone(rangeDays, tz) : undefined;

  const { data, isLoading, error } = useQuery<LoadsResponse>({
    // ``since`` is part of the KEY, not just the request: without it a
    // range change would serve the previous window's rows from cache.
    queryKey: ['loads', tab, since ?? 'all'],
    queryFn: () => listLoads(tab || undefined, since),
    // The status tab is part of the KEY, so switching it used to mean
    // "no cached data" -> isLoading -> the DataGrid UNMOUNTED and was
    // replaced by a skeleton.  That threw away every piece of state the
    // grid owns: the open pivot panel, collapsed pivot groups, the
    // selection.  It also blanked the tab counters to 0 for a second,
    // which states something false about every other tab.
    //
    // Keeping the previous page's data holds the whole surface still:
    // the grid stays mounted with the rows you were reading, the counts
    // keep their last value, and only the rows swap when the new page
    // lands.  isLoading now means what it says — the FIRST load.
    placeholderData: keepPreviousData,
  });

  const loads = useMemo(() => data?.loads ?? [], [data]);
  // "This ACCOUNT has no loads" and "this TAB has no loads" are different
  // answers and only the first one is the page's to give.  ``loads`` is a
  // server-filtered slice, so an empty Delivered tab used to satisfy the
  // page-level empty state — which UNMOUNTED the grid, and the tab strip
  // lives inside the grid now, so the control that got you there vanished
  // with it and the only way back was a reload.  Reported as a race; it
  // is deterministic — any tab with zero rows was a dead end.
  const accountEmpty = loads.length === 0 && !tab && !savedId;
  // Memoised: ``data?.counts ?? {}`` is a NEW object every render, which
  // would bust the total memo below on every parent render.
  const counts = useMemo<Record<string, number>>(() => data?.counts ?? {}, [data]);
  // What the CURRENT tab really holds, server-side.  The badges have
  // always known this; the grid did not, so its footer read "1-250 of
  // 500" on a Delivered tab whose own badge said 1,271 — two numbers on
  // one screen disagreeing, with the smaller one dressed as the total.
  //
  // ``totalRows`` is DataGrid's contract for exactly this (see its
  // CLAUDE.md, "Holding a slice? Say so").  It fixes far more than the
  // footer: without it sort silently orders the loaded 500 and reads as
  // sorted, export writes a fragment as if it were everything, and PIVOT
  // — which this page enables — summarises part of the data as a total,
  // with no rows on screen to notice the shortfall by.
  const tabTotal = useMemo(() => {
    if (!data?.truncated) return undefined;         // the grid holds it all
    if (tab) return counts[tab];
    const all = Object.values(counts).reduce((a, b) => a + b, 0);
    return all || undefined;
  }, [data?.truncated, counts, tab]);

  // Driver / dispatcher options for the layover dialog, derived from the
  // loads on screen (the people who actually run freight here) — avoids
  // a separate members endpoint the caller may not have permission for.
  const people = useMemo(() => {
    const drivers = new Map<number, string>();
    const dispatchers = new Map<number, string>();
    for (const l of loads) {
      if (l.driver_user_id && l.driver_name) drivers.set(l.driver_user_id, l.driver_name);
      if (l.dispatcher_user_id && l.dispatcher_name) dispatchers.set(l.dispatcher_user_id, l.dispatcher_name);
    }
    const opts = (m: Map<number, string>): PersonOption[] =>
      [...m.entries()].map(([id, name]) => ({ id, name }))
        .sort((a, b) => a.name.localeCompare(b.name));
    return { drivers: opts(drivers), dispatchers: opts(dispatchers) };
  }, [loads]);

  const refetch = () => qc.invalidateQueries({ queryKey: ['loads'] });

  return (
    // Full-height column so the header and status tabs keep their
    // natural size.  The grid no longer needs this — it measures the
    // room left below itself — but keeping it means the page's own
    // chrome can't be pushed off by a long toolbar.
    <div className="flex h-full flex-col min-h-0">
      <PageHeader
        icon={Package}
        title={t('nav.loads', 'Loads')}
        description={t(
          'loads_page.description',
          'Every load in one place — entered by hand or synced from your TMS. Widen the date range to reach older ones.',
        )}
        actions={(
          <div className="flex items-center gap-2">
            {/* A VIEW control, and the divider below says so: what
                follows it DOES something (books a deduction, creates a
                load), while this changes what you are LOOKING AT.  All
                three shared one shape and one group, so telling the
                filter from the actions meant reading all three.

                It narrows HISTORY only — the server exempts in-progress
                loads — and it renders for every viewer, because reading
                a narrower slice is not a management action. */}
            <InfoTip label={t(
              'loads_page.range_help',
              'Narrows completed loads and the tab counts. Loads still in progress always show, whatever the range.',
            )} />
            <DateRangePresets
              value={rangeDays}
              onChange={setRangeDays}
              options={RANGE_OPTIONS}
              // The custom calendar clamps to this; the shared default of
              // 90 would silently refuse the year-wide picks this page
              // exists to allow.
              maxDays={730}
            />
            {canManage && (
              <>
                <span aria-hidden className="h-5 w-px bg-border mx-1" />
                <Button variant="outline" onClick={() => setLayoverOpen(true)}>
                  <Clock className="mr-1.5" />
                  {t('loads_page.add_off_load', 'Off-load pay / deduction')}
                </Button>
                <Button onClick={() => { setEditing(null); setDialogOpen(true); }}>
                  <Plus className="mr-1.5" />
                  {t('loads_page.add', 'Add load')}
                </Button>
              </>
            )}
          </div>
        )}
      />


      {isLoading && <TableSkeleton />}
      {!isLoading && error != null && (
        <ErrorState message={error instanceof Error ? error.message : String(error)} />
      )}
      {!isLoading && error == null && accountEmpty && (
        <EmptyState
          icon={Package}
          title={t('loads_page.empty_title', 'No loads yet')}
          description={
            canManage
              ? t('loads_page.empty_manage', 'Add your first load, or connect your TMS on the Integrations page to sync them in.')
              : t('loads_page.empty_view', 'Loads will appear here once they are entered or synced.')
          }
        />
      )}
      {/* Rendered whenever the account HAS loads, empty tab or not — the
          grid draws its own "no rows" line and, crucially, keeps the tab
          strip on screen so the operator can leave. */}
      {!isLoading && error == null && !accountEmpty && (
        <DataGrid
          // ``tableId`` opts into the column-controls layer (3-dot
          // menu / pin / hide / reorder / Export / per-user layout).
          // Also what makes the ``filterable`` columns below actually
          // reachable — the filter popover opens from the 3-dot menu.
          tableId="loads"
          // Names the constraint that emptied it.  "No loads in this
          // status" is FALSE while a date window is on — there may be
          // forty, one range-widening away — and that sentence is where
          // someone hunting an old load gives up and calls it missing.
          emptyMessage={rangeDays > 0
            ? t(
              'loads_page.empty_tab_ranged',
              'No loads in this status in the selected date range. Widen the range to see older loads.',
            )
            : t('loads_page.empty_tab', 'No loads in this status.')}
          segments={LOAD_SEGMENTS}
          // ONE slot, two kinds of occupant (the AlertsResults pattern).
          segmentKey={savedId ? TAB_PREFIX + savedId : (tab || 'all')}
          onSegmentChange={(key, savedTab) => {
            if (savedTab) {
              // A saved tab restores the lifecycle slice it was captured
              // under (``baseSegment``); its own filters and search are
              // applied by the grid over that slice, which works because
              // this page does not pass ``manualFiltering`` — only the
              // STATUS is server-side.
              setSavedId(key.slice(TAB_PREFIX.length));
              const base = savedTab.baseSegment;
              setTab(base && base !== 'all' ? base : '');
              return;
            }
            setSavedId('');
            setTab(key === 'all' ? '' : key);
          }}
          // Authoritative, from the server — the grid holds one status
          // slice at a time, so tallying loaded rows would report 0 for
          // every tab except the open one.
          segmentCounts={counts}
          // Declares the slice.  Undefined when the grid holds everything,
          // so nothing changes on a tab that is not truncated.
          totalRows={tabTotal}
          columns={columns}
          pivot
          // Personal saved tabs — they capture the pivot config too, so a
          // built report can be kept rather than rebuilt each visit.
          savedTabs
          data={loads as unknown as Record<string, unknown>[]}
          searchKey="customer"
          searchPlaceholder={t('loads_page.search', 'Search customer…')}
          onRowClick={
            canManage
              ? (row) => {
                  setEditing(row as unknown as LoadRow);
                  setDialogOpen(true);
                }
              : undefined
          }
          // Right-click → Edit (gated by the same canEditLoad the row
          // click uses).  Action list lives in ./contextMenu.
          rowActions={(row) => loadRowMenu(row as unknown as LoadRow, {
            canEditLoad,
            openEdit: (r) => { setEditing(r); setDialogOpen(true); },
          })}
        />
      )}

      <LoadManageDialog
        open={dialogOpen}
        load={editing}
        onClose={() => setDialogOpen(false)}
        onSaved={refetch}
      />
      <LayoverDialog
        open={layoverOpen}
        drivers={people.drivers}
        dispatchers={people.dispatchers}
        onClose={() => setLayoverOpen(false)}
        onSaved={refetch}
      />
    </div>
  );
}
