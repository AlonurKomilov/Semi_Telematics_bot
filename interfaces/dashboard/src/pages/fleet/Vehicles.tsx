import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
import type { Vehicle, VehiclesResponse } from '../../types';
import type { AnyColumn } from '../../types';

type StatusFilter = 'all' | 'moving' | 'idle' | 'stopped';
const STATUS_OPTIONS: StatusFilter[] = ['all', 'moving', 'idle', 'stopped'];

const columns: AnyColumn[] = [
  { key: 'name', label: 'Vehicle' },
  { key: 'company', label: 'Company' },
  {
    key: 'status',
    label: 'Status',
    render: (v) => <StatusBadge status={v as string} />,
  },
  { key: 'address', label: 'Location' },
  {
    key: 'fuel_percent',
    label: 'Fuel',
    render: (v) => v != null ? `${Math.round(v as number)}%` : '—',
  },
  {
    key: 'def_percent',
    label: 'DEF',
    render: (v) => v != null ? `${Math.round(v as number)}%` : '—',
  },
  {
    key: 'fault_count',
    label: 'Faults',
    render: (v) => (v as number) > 0 ? <span className="text-orange-600 dark:text-orange-400 font-medium">{v as number}</span> : '0',
  },
];

export default function Vehicles() {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const navigate = useNavigate();

  // React Query (Phase E23): cache the vehicle list per status filter.
  // ``placeholderData: prev`` keeps the previous list visible while a new
  // filter is being fetched so the table doesn't flash blank.
  const { data, isLoading, error: queryError, refetch } = useQuery<VehiclesResponse>({
    queryKey: ['fleet-vehicles', statusFilter],
    queryFn: () => {
      const params = new URLSearchParams();
      if (statusFilter !== 'all') params.set('status', statusFilter);
      params.set('page_size', '200');
      return apiJSON<VehiclesResponse>(`/vehicles?${params}`);
    },
    placeholderData: (prev) => prev,
  });

  const vehicles  = data?.vehicles ?? [];
  const totalCount = (data as unknown as { count?: number } | undefined)?.count ?? vehicles.length;
  const loading   = isLoading && !data;
  const error     = queryError instanceof Error ? queryError.message : (queryError ? String(queryError) : '');

  // Summary counters — computed from the loaded vehicles.
  // When filtering by status the API returns only those vehicles,
  // so per-status counts reflect the full server result.
  const counts: Record<string, number> = { moving: 0, idle: 0, stopped: 0 };
  vehicles.forEach((v) => { if (v.status && counts[v.status] !== undefined) counts[v.status]++; });
  // "All" label uses the server's total count (may be larger than loaded when filtered)
  const allLabel = statusFilter === 'all' ? `All (${totalCount})` : `All (${totalCount})`;

  if (error) return <p className="text-destructive">{error}</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Vehicles</h1>
        <button
          onClick={() => refetch()}
          className="text-sm text-muted-foreground hover:text-foreground transition"
        >
          ↻ Refresh
        </button>
      </div>

      {/* Status summary */}
      <div className="flex gap-3 mb-4">
        {STATUS_OPTIONS.map((s) => {
          const label = s === 'all' ? allLabel : `${s} (${counts[s] || 0})`;
          return (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium capitalize transition ${
                statusFilter === s
                  ? 'bg-muted/80 text-foreground'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>

      {loading && vehicles.length === 0 ? (
        <p className="text-muted-foreground">Loading...</p>
      ) : (
        <DataTable
          columns={columns}
          data={vehicles as unknown as Record<string, unknown>[]}
          searchKey="name"
          onRowClick={(row) => navigate(`/fleet/vehicle/${encodeURIComponent(row.name as string)}`)}
        />
      )}
    </div>
  );
}
