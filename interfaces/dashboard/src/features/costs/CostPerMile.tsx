import { lazy, Suspense, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DollarSign } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { toneText, type Tone } from '../../lib/status';
import DataGrid from '../../components/DataGrid';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import type { CPMVehicle, CPMResponse, AnyColumn } from '../../types';

// recharts is ~120 KB gzipped — defer it so the table paints first.
const CpmChart = lazy(() => import('@/features/costs/CpmChart'));

const cols: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true, filterable: true },
  {
    key: 'total_miles', label: 'Miles', sortable: true,
    filterable: true, filterMode: 'range', filterRange: { min: 0, step: 500, unit: 'mi' },
    render: (v) => `${(v as number).toLocaleString()}`,
  },
  {
    key: 'total_gallons', label: 'Gallons', sortable: true,
    render: (v) => (v as number).toFixed(1),
  },
  {
    key: 'total_cost', label: 'Total Cost', sortable: true,
    filterable: true, filterMode: 'range', filterRange: { min: 0, step: 100, unit: '$' },
    render: (v) => `$${(v as number).toLocaleString()}`,
  },
  {
    key: 'cpm', label: 'Cost per Mile', sortable: true,
    // The triage filter: "which trucks cost more than $0.60/mi?"
    filterable: true, filterMode: 'range', filterRange: { min: 0, step: 0.05, unit: '$' },
    render: (v) => {
      const n = v as number;
      // Higher cost-per-mile is worse: danger above $0.60, warn above $0.40.
      const tone: Tone = n > 0.6 ? 'danger' : n > 0.4 ? 'warn' : 'ok';
      return <span className={toneText(tone)}>${n.toFixed(3)}</span>;
    },
  },
  {
    key: 'mpg', label: 'MPG', sortable: true,
    filterable: true, filterMode: 'range', filterRange: { min: 0, step: 0.5 },
    render: (v) => {
      const n = v as number;
      // Lower fuel economy is worse: danger below 5 MPG, warn below 7.
      const tone: Tone = n < 5 ? 'danger' : n < 7 ? 'warn' : 'ok';
      return <span className={toneText(tone)}>{n.toFixed(1)}</span>;
    },
  },
];

export default function CostPerMile() {
  const { t } = useTranslation();
  const [vehicles, setVehicles] = useState<CPMVehicle[]>([]);
  const [fleet, setFleet] = useState<{ avg_cpm: number; avg_mpg: number; total_miles: number; total_cost: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    apiJSON<CPMResponse>('/costs/cpm')
      .then((d) => {
        setVehicles(d.vehicles || []);
        setFleet({
          avg_cpm: d.aggregate_avg_cpm,
          avg_mpg: d.aggregate_avg_mpg,
          total_miles: d.aggregate_total_miles,
          total_cost: d.aggregate_total_cost,
        });
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed'))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <PageHeader
        icon={DollarSign}
        title={t('pages.cost_per_mile_title')}
        description={t('pages.cost_per_mile_desc')}
      />

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

      {error && <div className="mb-3"><ErrorState message={error} /></div>}

      {loading ? (
        <TableSkeleton rows={6} cols={6} />
      ) : vehicles.length === 0 ? (
        <EmptyState
          icon={DollarSign}
          title="No cost-per-mile data yet"
          description="Add at least 2 fuel entries per vehicle with odometer readings — we'll compute CPM as soon as we have enough samples."
        />
      ) : (
        <>
          <p className="text-sm text-muted-foreground mb-2">{vehicles.length} vehicle{vehicles.length !== 1 && 's'} tracked</p>

          {/* CPM bar chart */}
          <div className="bg-card border border-border rounded-xl p-5 mb-6">
            <p className="text-sm text-muted-foreground mb-3 font-medium">Cost per Mile by Vehicle</p>
            <Suspense fallback={<div className="h-[220px] bg-muted/40 rounded animate-pulse" />}>
              <CpmChart vehicles={vehicles} avgCpm={fleet?.avg_cpm ?? null} />
            </Suspense>
          </div>

          <DataGrid
            tableId="cost-per-mile"
            columns={cols}
            data={vehicles as unknown as Record<string, unknown>[]}
            searchKey="vehicle_name"
          />
        </>
      )}
    </div>
  );
}
