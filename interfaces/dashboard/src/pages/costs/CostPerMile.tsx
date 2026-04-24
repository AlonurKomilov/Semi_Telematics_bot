import { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts';
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
    key: 'cpm', label: 'Cost per Mile', sortable: true,
    render: (v) => {
      const n = v as number;
      const color = n > 0.6 ? 'text-destructive' : n > 0.4 ? 'text-yellow-700 dark:text-yellow-400' : 'text-green-600 dark:text-green-400';
      return <span className={color}>${n.toFixed(3)}</span>;
    },
  },
  {
    key: 'mpg', label: 'MPG', sortable: true,
    render: (v) => {
      const n = v as number;
      const color = n < 5 ? 'text-destructive' : n < 7 ? 'text-yellow-700 dark:text-yellow-400' : 'text-green-600 dark:text-green-400';
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
      <h1 className="text-2xl font-bold mb-6">Cost per Mile</h1>

      {/* Fleet summary cards */}
      {fleet && (
        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground">Fleet Avg CPM</p>
            <p className="text-xl font-bold">${fleet.avg_cpm.toFixed(3)}</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground">Fleet Avg MPG</p>
            <p className="text-xl font-bold">{fleet.avg_mpg.toFixed(1)}</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground">Total Miles</p>
            <p className="text-xl font-bold">{fleet.total_miles.toLocaleString()}</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground">Total Fuel Cost</p>
            <p className="text-xl font-bold">${fleet.total_cost.toLocaleString()}</p>
          </div>
        </div>
      )}

      {error && <p className="text-destructive text-sm mb-3">{error}</p>}

      {loading ? (
        <p className="text-muted-foreground">Loading...</p>
      ) : vehicles.length === 0 ? (
        <div className="bg-card border border-border rounded-xl p-8 text-center text-muted-foreground">
          <p className="text-4xl mb-3">📊</p>
          <p>No cost-per-mile data yet.</p>
          <p className="text-sm mt-1">Add at least 2 fuel entries per vehicle with odometer readings.</p>
        </div>
      ) : (
        <>
          <p className="text-sm text-muted-foreground mb-2">{vehicles.length} vehicle{vehicles.length !== 1 && 's'} tracked</p>

          {/* CPM bar chart */}
          <div className="bg-card border border-border rounded-xl p-5 mb-6">
            <p className="text-sm text-muted-foreground mb-3 font-medium">Cost per Mile by Vehicle</p>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart
                data={vehicles.slice(0, 15).map((v) => ({ name: v.vehicle_name, cpm: v.cpm }))}
                margin={{ top: 4, right: 20, left: 0, bottom: 40 }}
              >
                <XAxis dataKey="name" tick={{ fill: '#9ca3af', fontSize: 11 }} angle={-35} textAnchor="end" interval={0} />
                <YAxis tick={{ fill: '#6b7280', fontSize: 11 }} tickFormatter={(v) => `$${v.toFixed(2)}`} />
                <Tooltip
                  formatter={(v) => [`$${(v as number).toFixed(3)}`, 'CPM']}
                  contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8, color: '#f9fafb' }}
                />
                {fleet && <ReferenceLine y={fleet.avg_cpm} stroke="#6b7280" strokeDasharray="4 4" label={{ value: 'avg', fill: '#6b7280', fontSize: 11 }} />}
                <Bar dataKey="cpm" radius={[4, 4, 0, 0]}>
                  {vehicles.slice(0, 15).map((v) => (
                    <Cell key={v.vehicle_name} fill={v.cpm > 0.6 ? '#ef4444' : v.cpm > 0.4 ? '#eab308' : '#22c55e'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

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
