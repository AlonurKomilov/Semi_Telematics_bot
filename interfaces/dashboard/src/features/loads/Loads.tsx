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
import DataGrid from '../../components/datagrid';
import { loadRowMenu } from './contextMenu';
import {
  PageHeader, EmptyState, ErrorState, TableSkeleton,
} from '../../components/shell';
import { Button } from '../../components/ui/button';
import { useRoleView } from '../../context/RoleViewContext';
import { statusClasses } from '../../lib/status';
import type { AnyColumn } from '../../types';
import LoadManageDialog from './LoadManageDialog';
import LayoverDialog from './LayoverDialog';
import type { PersonOption } from './LayoverDialog';
import { LOAD_STATUSES, listLoads } from './api';
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

const TABS: { key: string; label: string }[] = [
  { key: '', label: 'All' },
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
  const [tab, setTab] = useState('');
  const [editing, setEditing] = useState<LoadRow | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [layoverOpen, setLayoverOpen] = useState(false);

  // Gate on the ACTIVE VIEW's permission so role-preview stays honest.
  // Any Manage holder edits ANY load (the own-scope wall came down
  // 2026-07-29) — the load's history trail answers "who changed what".
  const canManage = viewHas('can_manage_loads');
  const canEditLoad = (_row: LoadRow): boolean => canManage;

  const { data, isLoading, error } = useQuery<LoadsResponse>({
    queryKey: ['loads', tab],
    queryFn: () => listLoads(tab || undefined),
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
  const counts = data?.counts ?? {};

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
    // Full-height column: header + status tabs keep their natural size
    // and the grid takes the rest, so the LIST scrolls inside its own
    // card instead of the page growing to 250 rows.  ``h-full`` works
    // without any measurement because the shell's content region is
    // already a definite height (``h-screen overflow-hidden`` + one
    // scroll region) — see the ``fillHeight`` prop on DataGrid.
    <div className="flex h-full flex-col min-h-0">
      <PageHeader
        icon={Package}
        title={t('nav.loads', 'Loads')}
        description={t(
          'loads_page.description',
          'Every load in one place — entered by hand or synced from your TMS.',
        )}
        actions={canManage ? (
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => setLayoverOpen(true)}>
              <Clock size={16} className="mr-1.5" />
              {t('loads_page.add_off_load', 'Off-load pay / deduction')}
            </Button>
            <Button onClick={() => { setEditing(null); setDialogOpen(true); }}>
              <Plus size={16} className="mr-1.5" />
              {t('loads_page.add', 'Add load')}
            </Button>
          </div>
        ) : undefined}
      />

      {/* Status tabs with live counts. */}
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        {TABS.map(({ key, label }) => {
          const active = tab === key;
          const count = key ? counts[key] ?? 0 : undefined;
          return (
            <button
              key={key || 'all'}
              type="button"
              onClick={() => setTab(key)}
              className={`px-3 py-1.5 rounded-md text-sm border transition-colors ${
                active
                  ? 'bg-primary text-primary-foreground border-primary'
                  : 'bg-card text-foreground border-border hover:border-ring'
              }`}
            >
              {label}
              {/* ``0`` is an ANSWER — "no loads are upcoming" — and
                  hiding the badge for it made that tab look identical
                  to "All", which carries no badge because it is never
                  counted.  Same pixels, two different meanings.  Keyed
                  tabs always show their number; only "All" stays bare. */}
              {count != null && (
                <span className={`ml-1.5 text-xs ${active ? 'text-primary-foreground/80' : 'text-muted-foreground'}`}>
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {isLoading && <TableSkeleton />}
      {!isLoading && error != null && (
        <ErrorState message={error instanceof Error ? error.message : String(error)} />
      )}
      {!isLoading && error == null && loads.length === 0 && (
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
      {!isLoading && error == null && data?.truncated && (
        <p className="mb-2 text-xs text-muted-foreground">
          {t(
            'loads_page.truncated',
            'Showing the latest 500 loads — use the status tabs or search to narrow further.',
          )}
        </p>
      )}
      {!isLoading && error == null && loads.length > 0 && (
        <DataGrid
          // ``tableId`` opts into the column-controls layer (3-dot
          // menu / pin / hide / reorder / Export / per-user layout).
          // Also what makes the ``filterable`` columns below actually
          // reachable — the filter popover opens from the 3-dot menu.
          tableId="loads"
          columns={columns}
          pivot
          fillHeight
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
