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
import { Package, Plus } from 'lucide-react';
import DataTable from '../../components/DataTable';
import {
  PageHeader, EmptyState, ErrorState, TableSkeleton,
} from '../../components/shell';
import { Button } from '../../components/ui/button';
import { useRoleView } from '../../context/RoleViewContext';
import { statusClasses } from '../../lib/status';
import type { AnyColumn } from '../../types';
import LoadManageDialog from './LoadManageDialog';
import { LOAD_STATUSES, listLoads } from './api';
import type { LoadRow, LoadsResponse } from './api';

function Pill({ value }: { value: unknown }) {
  const v = String(value || '');
  if (!v) return <span className="text-muted-foreground">—</span>;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium capitalize ${statusClasses(v)}`}>
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
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium capitalize ${statusClasses('connected')}`}>
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
  { key: 'delivery_date', label: 'DEL date', sortable: true, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  { key: 'total_rate', label: 'Rate', sortable: true, render: (v) => <MoneyCell value={v} /> },
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

  // Gate on the ACTIVE VIEW's permission so role-preview stays honest.
  const canManage = viewHas('can_manage_loads');

  const { data, isLoading, error } = useQuery<LoadsResponse>({
    queryKey: ['loads', tab],
    queryFn: () => listLoads(tab || undefined),
  });

  const loads = useMemo(() => data?.loads ?? [], [data]);
  const counts = data?.counts ?? {};

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
          <Button onClick={() => { setEditing(null); setDialogOpen(true); }}>
            <Plus size={16} className="mr-1.5" />
            {t('loads_page.add', 'Add load')}
          </Button>
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
      {!isLoading && error == null && loads.length > 0 && (
        <DataTable
          columns={COLUMNS}
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
        />
      )}

      <LoadManageDialog
        open={dialogOpen}
        load={editing}
        onClose={() => setDialogOpen(false)}
        onSaved={refetch}
      />
    </div>
  );
}
