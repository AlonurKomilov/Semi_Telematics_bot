import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Fuel, Plus } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useTimezone } from '../../hooks/useTimezone';
import { todayInTimeZone } from '../../utils/datetime';
import DataTable from '../../components/DataTable';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import type {
  FuelEntry, FuelEntriesResponse,
  FuelSummaryVehicle, FuelSummaryResponse,
  AnyColumn,
} from '../../types';

const entryCols: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true },
  { key: 'date', label: 'Date', sortable: true },
  { key: 'gallons', label: 'Gallons', sortable: true },
  {
    key: 'price_per_gallon', label: '$/Gal', sortable: true,
    render: (v) => `$${(v as number).toFixed(3)}`,
  },
  {
    key: 'total_cost', label: 'Total', sortable: true,
    render: (v) => `$${(v as number).toFixed(2)}`,
  },
  {
    key: 'odometer_miles', label: 'Odometer', sortable: true,
    render: (v) => `${(v as number).toLocaleString()} mi`,
  },
];

const summaryCols: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true },
  { key: 'entries', label: 'Entries', sortable: true },
  { key: 'total_gallons', label: 'Gallons', sortable: true },
  {
    key: 'total_cost', label: 'Total Cost', sortable: true,
    render: (v) => `$${(v as number).toFixed(2)}`,
  },
  {
    key: 'avg_price', label: 'Avg $/Gal', sortable: true,
    render: (v) => `$${(v as number).toFixed(3)}`,
  },
];

export default function FuelCosts() {
  const { t } = useTranslation();
  // "Today" must be the account's local day (not UTC) — a fuel entry
  // logged late evening on the West Coast belongs to today, and the
  // date input must not block it as "future".
  const tz = useTimezone();
  const today = () => todayInTimeZone(tz);
  const [tab, setTab] = useState<'entries' | 'summary'>('entries');
  const [entries, setEntries] = useState<FuelEntry[]>([]);
  const [summaryData, setSummaryData] = useState<FuelSummaryVehicle[]>([]);
  const [aggregateCost, setAggregateCost] = useState(0);
  const [aggregateGallons, setAggregateGallons] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(false);

  // Form state
  const [fVehicle, setFVehicle] = useState('');
  const [fGallons, setFGallons] = useState('');
  const [fPrice, setFPrice] = useState('');
  const [fOdo, setFOdo] = useState('');
  const [fDate, setFDate] = useState(today);
  const [saving, setSaving] = useState(false);

  const fetchData = useCallback(() => {
    setLoading(true);
    setError('');
    if (tab === 'entries') {
      apiJSON<FuelEntriesResponse>('/costs/fuel')
        .then((d) => setEntries(d.entries || []))
        .catch((e) => setError(e instanceof Error ? e.message : 'Failed'))
        .finally(() => setLoading(false));
    } else {
      apiJSON<FuelSummaryResponse>('/costs/fuel/summary')
        .then((d) => {
          setSummaryData(d.vehicles || []);
          setAggregateCost(d.aggregate_total_cost);
          setAggregateGallons(d.aggregate_total_gallons);
        })
        .catch((e) => setError(e instanceof Error ? e.message : 'Failed'))
        .finally(() => setLoading(false));
    }
  }, [tab]);

  useEffect(() => { fetchData(); }, [fetchData]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!fVehicle || !fGallons || !fPrice || !fOdo) return;
    setSaving(true);
    setError('');
    try {
      await apiJSON('/costs/fuel', {
        method: 'POST',
        body: {
          vehicle_name: fVehicle,
          gallons: parseFloat(fGallons),
          price_per_gallon: parseFloat(fPrice),
          odometer_miles: parseFloat(fOdo),
          date: fDate,
        },
      });
      setShowForm(false);
      setFVehicle(''); setFGallons(''); setFPrice(''); setFOdo(''); setFDate(today());
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        icon={Fuel}
        title={t('pages.fuel_costs_title')}
        description={t('pages.fuel_costs_desc')}
        actions={
          <button
            onClick={() => setShowForm(!showForm)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition"
          >
            <Plus size={14} />
            {showForm ? 'Cancel' : 'Add entry'}
          </button>
        }
      />

      {/* Add form */}
      {showForm && (
        <form onSubmit={handleSubmit} className="bg-card border border-border rounded-xl p-4 mb-6 grid grid-cols-5 gap-3">
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Vehicle</label>
            <input
              type="text" required value={fVehicle} onChange={(e) => setFVehicle(e.target.value)}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Gallons</label>
            <input
              type="number" step="0.1" min="0" required value={fGallons} onChange={(e) => setFGallons(e.target.value)}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">$/Gallon</label>
            <input
              type="number" step="0.001" min="0" required value={fPrice} onChange={(e) => setFPrice(e.target.value)}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Odometer</label>
            <input
              type="number" step="1" min="0" required value={fOdo} onChange={(e) => setFOdo(e.target.value)}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </div>
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <label className="block text-xs text-muted-foreground mb-1">Date</label>
              <input
                type="date" required value={fDate} max={today()} onChange={(e) => setFDate(e.target.value)}
                className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
              />
            </div>
            <button
              type="submit" disabled={saving}
              className="px-4 py-1.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium text-primary-foreground transition whitespace-nowrap"
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </form>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-4">
        {(['entries', 'summary'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition capitalize ${
              tab === t ? 'bg-muted/80 text-foreground' : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Summary cards (summary tab) */}
      {tab === 'summary' && summaryData.length > 0 && (
        <div className="grid grid-cols-3 gap-4 mb-4">
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground">Total Spent</p>
            <p className="text-xl font-bold">${aggregateCost.toLocaleString()}</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground">Total Gallons</p>
            <p className="text-xl font-bold">{aggregateGallons.toLocaleString()}</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground">Avg $/Gallon</p>
            <p className="text-xl font-bold">
              ${aggregateGallons > 0 ? (aggregateCost / aggregateGallons).toFixed(3) : '—'}
            </p>
          </div>
        </div>
      )}

      {error && <div className="mb-3"><ErrorState message={error} /></div>}

      {loading ? (
        <TableSkeleton rows={6} cols={5} />
      ) : (tab === 'entries' ? entries : summaryData).length === 0 ? (
        <EmptyState
          icon={Fuel}
          title={tab === 'entries' ? 'No fuel entries yet' : 'No fuel data to summarize'}
          description={tab === 'entries' ? 'Log your first fill-up — gallons, price, and odometer reading.' : 'Add fuel entries to see per-vehicle totals.'}
          action={
            tab === 'entries' ? (
              <button
                onClick={() => setShowForm(true)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition"
              >
                <Plus size={14} />
                Add entry
              </button>
            ) : undefined
          }
        />
      ) : (
        <DataTable
          columns={tab === 'entries' ? entryCols : summaryCols}
          data={(tab === 'entries' ? entries : summaryData) as unknown as Record<string, unknown>[]}
          searchKey="vehicle_name"
        />
      )}
    </div>
  );
}
