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
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Clock, Package, Plus } from 'lucide-react';
import DataGrid from '../../components/datagrid';
import {
  PageHeader, EmptyState, ErrorState, TableSkeleton,
} from '../../components/shell';
import { Button } from '../../components/ui/button';
import { useRoleView } from '../../context/RoleViewContext';
import { useAuth } from '../../context/AuthContext';
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

function MoneyCell({ value }: { value: unknown }) {
  if (value == null || value === '') return <span className="text-muted-foreground">—</span>;
  const n = Number(value);
  return (
    <span className="tabular-nums font-medium">
      ${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
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

const COLUMNS: AnyColumn[] = [
  { key: 'seq', label: 'ID', sortable: true, render: (v) => (v ? <span className="font-mono text-xs text-muted-foreground">{`#${v}`}</span> : <span className="text-muted-foreground">—</span>) },
  { key: 'load_number', label: 'Load #', sortable: true, render: (v) => (v ? <span className="font-medium">{String(v)}</span> : <span className="text-muted-foreground">—</span>) },
  { key: 'customer', label: 'Customer', sortable: true, filterable: true, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  { key: 'driver_name', label: 'Driver', sortable: true, filterable: true, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  { key: 'vehicle_unit', label: 'Truck', sortable: true, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  { key: 'trailer_unit', label: 'Trailer', sortable: true, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  { key: 'company_code', label: 'Company', sortable: true, filterable: true, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  { key: 'pickup_location', label: 'Pickup', sortable: true, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  { key: 'delivery_location', label: 'Delivery', sortable: true, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  {
    key: 'delivery_date', label: 'DEL date', sortable: true,
    // Date aggregation: earliest / latest delivery across the filtered
    // (or grouped) loads.  aggType 'date' hides sum/avg from the menu
    // and formats the min/max as a day in the account timezone.
    aggregable: true,
    aggType: 'date',
    render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>),
  },
  {
    key: 'total_rate', label: 'Rate', sortable: true,
    // Aggregable: operators total / average the rate across the filtered
    // loads via the ⋮ menu → footer total.  No ``|| 0`` on aggValue — a
    // load with no rate yet yields NaN, which DataGrid's Number.isFinite
    // filter drops from sum/avg/min/max (so it never counts as a $0 trip
    // in the Average); ``count`` still counts every filtered row.
    aggregable: true,
    aggFns: ['sum', 'avg', 'max', 'count'],
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
  { key: 'status', label: 'Status', sortable: true, render: (v) => <Pill value={v} /> },
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
  const { viewHas } = useRoleView();
  const qc = useQueryClient();
  const [tab, setTab] = useState('');
  const [editing, setEditing] = useState<LoadRow | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [layoverOpen, setLayoverOpen] = useState(false);

  // Gate on the ACTIVE VIEW's permission so role-preview stays honest.
  const canManage = viewHas('can_manage_loads');
  // Own-scope dispatchers manage only THEIR loads; managers manage any.
  // Mirrors the backend rule so the UI doesn't offer an edit the server
  // would 404 — the server remains the authority.
  const manageAll = viewHas('can_loads_manage_all');
  const { user: authUser } = useAuth();
  const myId = authUser?.id;
  const canEditLoad = (row: LoadRow): boolean =>
    canManage && (manageAll || (row.dispatcher_user_id != null && row.dispatcher_user_id === myId));

  const { data, isLoading, error } = useQuery<LoadsResponse>({
    queryKey: ['loads', tab],
    queryFn: () => listLoads(tab || undefined),
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
    <div>
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
              {count != null && count > 0 && (
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
          columns={COLUMNS}
          data={loads as unknown as Record<string, unknown>[]}
          searchKey="customer"
          searchPlaceholder={t('loads_page.search', 'Search customer…')}
          onRowClick={
            canManage
              ? (row) => {
                  const r = row as unknown as LoadRow;
                  if (!canEditLoad(r)) return;   // not yours — server would 404
                  setEditing(r);
                  setDialogOpen(true);
                }
              : undefined
          }
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
