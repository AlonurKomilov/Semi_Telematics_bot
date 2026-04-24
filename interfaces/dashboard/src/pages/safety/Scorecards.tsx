import { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import type { Scorecard, ScorecardsResponse, AnyColumn } from '../../types';

function EcoBadge({ pct }: { pct: number }) {
  const cls =
    pct >= 80 ? 'bg-green-500/15 text-green-700 dark:text-green-400' :
    pct >= 60 ? 'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400' :
    'bg-red-500/15 text-red-700 dark:text-red-400';
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
      return m > 10 ? <span className="text-destructive">{m}</span> : <span>{m}</span>;
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

  if (error && cards.length === 0) return <p className="text-destructive">{error}</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Driver Scorecards</h1>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="bg-muted border border-border rounded px-3 py-2 text-sm text-foreground/80"
        >
          {[7, 14, 30].map((d) => (
            <option key={d} value={d}>{d} days</option>
          ))}
        </select>
      </div>

      {/* Summary cards */}
      {cards.length > 0 && (
        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground">Drivers</p>
            <p className="text-xl font-bold">{cards.length}</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground">Avg Eco Score</p>
            <p className="text-xl font-bold">
              {cards.length ? (cards.reduce((s, c) => s + c.eco_pct, 0) / cards.length).toFixed(0) : 0}%
            </p>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground">Total Miles</p>
            <p className="text-xl font-bold">{cards.reduce((s, c) => s + c.miles, 0).toLocaleString()}</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground">Avg MPG</p>
            <p className="text-xl font-bold">
              {cards.length ? (cards.reduce((s, c) => s + c.mpg, 0) / cards.length).toFixed(1) : 0}
            </p>
          </div>
        </div>
      )}

      {loading ? (
        <p className="text-muted-foreground">Loading...</p>
      ) : (
        <>
          {/* Eco Score bar chart — top 10 drivers */}
          {cards.length > 0 && (
            <div className="bg-card border border-border rounded-xl p-5 mb-6">
              <p className="text-sm text-muted-foreground mb-3 font-medium">Eco Score by Driver</p>
              <ResponsiveContainer width="100%" height={Math.min(cards.length, 10) * 30 + 40}>
                <BarChart
                  layout="vertical"
                  data={[...cards].sort((a, b) => b.eco_pct - a.eco_pct).slice(0, 10).map((c) => ({
                    name: c.driver_name,
                    score: c.eco_pct,
                  }))}
                  margin={{ top: 0, right: 30, left: 0, bottom: 0 }}
                >
                  <XAxis type="number" domain={[0, 100]} tick={{ fill: '#6b7280', fontSize: 11 }} />
                  <YAxis type="category" dataKey="name" width={120} tick={{ fill: '#9ca3af', fontSize: 12 }} />
                  <Tooltip
                    formatter={(v) => [`${v}%`, 'Eco Score']}
                    contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8, color: '#f9fafb' }}
                  />
                  <Bar dataKey="score" radius={[0, 4, 4, 0]}>
                    {[...cards].sort((a, b) => b.eco_pct - a.eco_pct).slice(0, 10).map((c) => (
                      <Cell key={c.driver_name} fill={c.eco_pct >= 80 ? '#22c55e' : c.eco_pct >= 60 ? '#eab308' : '#ef4444'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
          <DataTable
          columns={columns}
          data={cards as unknown as Record<string, unknown>[]}
          searchKey="driver_name"
          onRowClick={(row) => setDetail(row as unknown as Scorecard)}
        />
        </>
      )}

      {/* Detail drawer */}
      {detail && (
        <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={() => setDetail(null)}>
          <div
            className="w-96 bg-card border-l border-border p-6 overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">{detail.driver_name}</h2>
              <button onClick={() => setDetail(null)} className="text-muted-foreground hover:text-foreground">✕</button>
            </div>
            {detail.company && <p className="text-sm text-muted-foreground mb-4">{detail.company}</p>}
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
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
