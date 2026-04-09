import { useEffect, useState, useCallback } from 'react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
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

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function FuelCosts() {
  const [tab, setTab] = useState<'entries' | 'summary'>('entries');
  const [entries, setEntries] = useState<FuelEntry[]>([]);
  const [summaryData, setSummaryData] = useState<FuelSummaryVehicle[]>([]);
  const [fleetCost, setFleetCost] = useState(0);
  const [fleetGallons, setFleetGallons] = useState(0);
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
          setFleetCost(d.fleet_total_cost);
          setFleetGallons(d.fleet_total_gallons);
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
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Fuel Costs</h1>
        <button
          onClick={() => setShowForm(!showForm)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition"
        >
          {showForm ? 'Cancel' : '+ Add Entry'}
        </button>
      </div>

      {/* Add form */}
      {showForm && (
        <form onSubmit={handleSubmit} className="bg-gray-900 border border-gray-800 rounded-xl p-4 mb-6 grid grid-cols-5 gap-3">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Vehicle</label>
            <input
              type="text" required value={fVehicle} onChange={(e) => setFVehicle(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Gallons</label>
            <input
              type="number" step="0.1" min="0" required value={fGallons} onChange={(e) => setFGallons(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">$/Gallon</label>
            <input
              type="number" step="0.001" min="0" required value={fPrice} onChange={(e) => setFPrice(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Odometer</label>
            <input
              type="number" step="1" min="0" required value={fOdo} onChange={(e) => setFOdo(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500"
            />
          </div>
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <label className="block text-xs text-gray-400 mb-1">Date</label>
              <input
                type="date" required value={fDate} max={today()} onChange={(e) => setFDate(e.target.value)}
                className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500"
              />
            </div>
            <button
              type="submit" disabled={saving}
              className="px-4 py-1.5 bg-green-600 hover:bg-green-700 disabled:opacity-50 rounded text-sm font-medium transition whitespace-nowrap"
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
              tab === t ? 'bg-gray-700 text-white' : 'text-gray-400 hover:text-white'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Summary cards (summary tab) */}
      {tab === 'summary' && summaryData.length > 0 && (
        <div className="grid grid-cols-3 gap-4 mb-4">
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <p className="text-xs text-gray-400">Total Spent</p>
            <p className="text-xl font-bold">${fleetCost.toLocaleString()}</p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <p className="text-xs text-gray-400">Total Gallons</p>
            <p className="text-xl font-bold">{fleetGallons.toLocaleString()}</p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <p className="text-xs text-gray-400">Avg $/Gallon</p>
            <p className="text-xl font-bold">
              ${fleetGallons > 0 ? (fleetCost / fleetGallons).toFixed(3) : '—'}
            </p>
          </div>
        </div>
      )}

      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

      {loading ? (
        <p className="text-gray-500">Loading...</p>
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
