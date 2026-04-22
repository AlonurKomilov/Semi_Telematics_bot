import { useEffect, useState } from 'react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import type { Scorecard, ScorecardsResponse, AnyColumn } from '../../types';

function EcoBadge({ pct }: { pct: number }) {
  const cls =
    pct >= 80 ? 'bg-green-500/20 text-green-400' :
    pct >= 60 ? 'bg-yellow-500/20 text-yellow-400' :
    'bg-red-500/20 text-red-400';
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>{pct}%</span>;
}

const columns: AnyColumn[] = [
  { key: 'driver_name', label: 'Driver', sortable: true },
  { key: 'company', label: 'Company', sortable: true },
  { key: 'miles', label: 'Miles', sortable: true, render: (v) => `${(v as number).toLocaleString()}` },
  { key: 'mpg', label: 'MPG', sortable: true },
  {
    key: 'eco_pct',
    label: 'Eco Score',
    sortable: true,
    render: (v) => <EcoBadge pct={v as number} />,
  },
  { key: 'drive_hours', label: 'Drive (h)', sortable: true },
  { key: 'idle_hours', label: 'Idle (h)', sortable: true },
  {
    key: 'overspeed_min',
    label: 'Overspeed (min)',
    sortable: true,
    render: (v) => {
      const m = v as number;
      return m > 10 ? <span className="text-red-400">{m}</span> : <span>{m}</span>;
    },
  },
  {
    key: 'anticipatory_braking_pct',
    label: 'Antic. Brake %',
    sortable: true,
    render: (v) => `${v}%`,
  },
];

export default function Scorecards() {
  const [cards, setCards] = useState<Scorecard[]>([]);
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState<Scorecard | null>(null);

  useEffect(() => {
    setLoading(true);
    apiJSON<ScorecardsResponse>(`/safety/scorecards?days=${days}`)
      .then((d) => setCards(d.scorecards || []))
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, [days]);

  if (error && cards.length === 0) return <p className="text-red-400">{error}</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Driver Scorecards</h1>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-300"
        >
          {[7, 14, 30].map((d) => (
            <option key={d} value={d}>{d} days</option>
          ))}
        </select>
      </div>

      {/* Summary cards */}
      {cards.length > 0 && (
        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <p className="text-xs text-gray-400">Drivers</p>
            <p className="text-xl font-bold">{cards.length}</p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <p className="text-xs text-gray-400">Avg Eco Score</p>
            <p className="text-xl font-bold">
              {cards.length ? (cards.reduce((s, c) => s + c.eco_pct, 0) / cards.length).toFixed(0) : 0}%
            </p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <p className="text-xs text-gray-400">Total Miles</p>
            <p className="text-xl font-bold">{cards.reduce((s, c) => s + c.miles, 0).toLocaleString()}</p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <p className="text-xs text-gray-400">Avg MPG</p>
            <p className="text-xl font-bold">
              {cards.length ? (cards.reduce((s, c) => s + c.mpg, 0) / cards.length).toFixed(1) : 0}
            </p>
          </div>
        </div>
      )}

      {loading ? (
        <p className="text-gray-500">Loading...</p>
      ) : (
        <DataTable
          columns={columns}
          data={cards as unknown as Record<string, unknown>[]}
          searchKey="driver_name"
          onRowClick={(row) => setDetail(row as unknown as Scorecard)}
        />
      )}

      {/* Detail drawer */}
      {detail && (
        <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={() => setDetail(null)}>
          <div
            className="w-96 bg-gray-900 border-l border-gray-800 p-6 overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">{detail.driver_name}</h2>
              <button onClick={() => setDetail(null)} className="text-gray-500 hover:text-white">✕</button>
            </div>
            {detail.company && <p className="text-sm text-gray-400 mb-4">{detail.company}</p>}
            <dl className="space-y-3 text-sm">
              <Stat label="Eco Score" value={`${detail.eco_pct}%`} />
              <Stat label="Miles Driven" value={detail.miles.toLocaleString()} />
              <Stat label="Fuel Economy" value={`${detail.mpg} MPG`} />
              <Stat label="Drive Time" value={`${detail.drive_hours}h (${detail.drive_pct}%)`} />
              <Stat label="Idle Time" value={`${detail.idle_hours}h (${detail.idle_pct}%)`} />
              <Stat label="Overspeed" value={`${detail.overspeed_min} min`} />
              <Stat label="Coasting" value={`${detail.coast_min} min`} />
              <Stat label="Cruise Control" value={`${detail.cruise_min} min`} />
              <Stat label="Anticipatory Brake" value={`${detail.anticipatory_braking_pct}%`} />
            </dl>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <dt className="text-gray-400">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
