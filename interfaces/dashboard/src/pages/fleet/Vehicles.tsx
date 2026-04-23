import { useEffect, useState, useCallback } from 'react';
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
    render: (v) => (v as number) > 0 ? <span className="text-orange-400 font-medium">{v as number}</span> : '0',
  },
];

export default function Vehicles() {
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  const fetchVehicles = useCallback(() => {
    setLoading(true);
    const params = new URLSearchParams();
    if (statusFilter !== 'all') params.set('status', statusFilter);
    // Request up to 200 vehicles per page so the list shows the full fleet.
    // For very large fleets, replace this with a proper pagination UI.
    params.set('page_size', '200');
    apiJSON<VehiclesResponse>(`/fleet/vehicles?${params}`)
      .then((d) => {
        setVehicles(d.vehicles || []);
        setTotalCount((d as unknown as { count: number }).count ?? d.vehicles?.length ?? 0);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [statusFilter]);

  useEffect(() => { fetchVehicles(); }, [fetchVehicles]);

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
          onClick={fetchVehicles}
          className="text-sm text-muted-foreground hover:text-white transition"
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
                  : 'text-muted-foreground hover:text-white hover:bg-muted'
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
