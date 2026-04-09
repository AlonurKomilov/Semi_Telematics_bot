import { useEffect, useState } from 'react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import type { CPMVehicle, CPMResponse, AnyColumn } from '../../types';

const cols: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true },
  {
    key: 'total_miles', label: 'Miles', sortable: true,
    render: (v) => `${(v as number).toLocaleString()}`,
  },
  {
    key: 'total_gallons', label: 'Gallons', sortable: true,
    render: (v) => (v as number).toFixed(1),
  },
  {
    key: 'total_cost', label: 'Total Cost', sortable: true,
    render: (v) => `$${(v as number).toLocaleString()}`,
  },
  {
    key: 'cpm', label: 'Cost/Mile', sortable: true,
    render: (v) => {
      const n = v as number;
      const color = n > 0.6 ? 'text-red-400' : n > 0.4 ? 'text-yellow-400' : 'text-green-400';
      return <span className={color}>${n.toFixed(3)}</span>;
    },
  },
  {
    key: 'mpg', label: 'MPG', sortable: true,
    render: (v) => {
      const n = v as number;
      const color = n < 5 ? 'text-red-400' : n < 7 ? 'text-yellow-400' : 'text-green-400';
      return <span className={color}>{n.toFixed(1)}</span>;
    },
  },
];

export default function CostPerMile() {
  const [vehicles, setVehicles] = useState<CPMVehicle[]>([]);
  const [fleet, setFleet] = useState<{ avg_cpm: number; avg_mpg: number; total_miles: number; total_cost: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    apiJSON<CPMResponse>('/costs/cpm')
      .then((d) => {
        setVehicles(d.vehicles || []);
        setFleet({
          avg_cpm: d.fleet_avg_cpm,
          avg_mpg: d.fleet_avg_mpg,
          total_miles: d.fleet_total_miles,
          total_cost: d.fleet_total_cost,
        });
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed'))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Cost Per Mile</h1>

      {/* Fleet summary cards */}
      {fleet && (
        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <p className="text-xs text-gray-400">Fleet Avg CPM</p>
            <p className="text-xl font-bold">${fleet.avg_cpm.toFixed(3)}</p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <p className="text-xs text-gray-400">Fleet Avg MPG</p>
            <p className="text-xl font-bold">{fleet.avg_mpg.toFixed(1)}</p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <p className="text-xs text-gray-400">Total Miles</p>
            <p className="text-xl font-bold">{fleet.total_miles.toLocaleString()}</p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <p className="text-xs text-gray-400">Total Fuel Cost</p>
            <p className="text-xl font-bold">${fleet.total_cost.toLocaleString()}</p>
          </div>
        </div>
      )}

      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

      {loading ? (
        <p className="text-gray-500">Loading...</p>
      ) : vehicles.length === 0 ? (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-8 text-center text-gray-500">
          <p className="text-4xl mb-3">📊</p>
          <p>No cost-per-mile data yet.</p>
          <p className="text-sm mt-1">Add at least 2 fuel entries per vehicle with odometer readings.</p>
        </div>
      ) : (
        <>
          <p className="text-sm text-gray-400 mb-2">{vehicles.length} vehicle{vehicles.length !== 1 && 's'} tracked</p>
          <DataTable
            columns={cols}
            data={vehicles as unknown as Record<string, unknown>[]}
            searchKey="vehicle_name"
          />
        </>
      )}
    </div>
  );
}
