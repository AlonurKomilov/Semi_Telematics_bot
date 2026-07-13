/**
 * Vendors — the per-account repair-vendor registry list.
 *
 * One row per real-world shop (master data), with usage rollups from
 * the linked work orders.  Click-through opens the vendor profile
 * (spend history + merge).  See docs/architecture/
 * vendor-parts-master-data.md (Phase A).
 */
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Store } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataGrid from '../../components/DataGrid';
import { PageHeader, EmptyState, ErrorState, TableSkeleton } from '../../components/shell';
import type { Vendor, AnyColumn } from '../../types';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';

function money(v: unknown): string {
  return `$${Number(v ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

const columns = (tz: string): AnyColumn[] => [
  { key: 'name', label: 'Vendor', sortable: true },
  {
    key: 'phone', label: 'Phone', sortable: false,
    render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>),
  },
  {
    key: 'address', label: 'Address', sortable: false,
    render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>),
  },
  {
    key: 'work_order_count', label: 'Work Orders', sortable: true,
    render: (v) => <span className="tabular-nums">{String(v ?? 0)}</span>,
  },
  {
    key: 'total_spent', label: 'Total Spent', sortable: true,
    render: (v) => <span className="tabular-nums font-medium">{money(v)}</span>,
  },
  {
    key: 'last_service_date', label: 'Last Visit', sortable: true,
    filterable: true, filterMode: 'date-range',
    render: (v) => (v
      ? formatDay(String(v), { timeZone: tz })
      : <span className="text-muted-foreground">—</span>),
  },
];

export default function Vendors() {
  const navigate = useNavigate();
  const tz = useTimezone();
  const { data, isLoading, error } = useQuery<{ vendors: Vendor[] }>({
    queryKey: ['vendors'],
    queryFn: () => apiJSON<{ vendors: Vendor[] }>('/vendors'),
  });
  const vendors = data?.vendors ?? [];
  const fetchError = error instanceof Error ? error.message : '';

  return (
    <div>
      <PageHeader
        icon={Store}
        title="Vendors"
        description="Every repair shop your fleet uses — one record per vendor, with spend history rolled up from work orders."
      />
      {fetchError && vendors.length === 0 ? (
        <ErrorState message={fetchError} />
      ) : isLoading && vendors.length === 0 ? (
        <TableSkeleton rows={6} cols={6} />
      ) : vendors.length === 0 ? (
        <EmptyState
          icon={Store}
          title="No vendors yet"
          description="Vendors appear here automatically as you record work orders — every saved invoice links its shop to the registry."
        />
      ) : (
        <DataGrid
          tableId="vendors"
          columns={columns(tz)}
          data={vendors as unknown as Record<string, unknown>[]}
          searchKey={['name', 'phone', 'address', 'email']}
          searchPlaceholder="Search vendors…"
          onRowClick={(row) => navigate(`/vendors/${(row as unknown as Vendor).id}`)}
        />
      )}
    </div>
  );
}
