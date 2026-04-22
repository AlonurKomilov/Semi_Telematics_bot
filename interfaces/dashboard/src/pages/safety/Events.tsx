import { useEffect, useState } from 'react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import type { SafetyEvent, SafetyEventsResponse, EventsSummary, AnyColumn } from '../../types';

const EVENT_TYPES = ['all', 'crash', 'braking', 'harshTurn', 'laneDeparture', 'followingDistance', 'rollingStop', 'acceleration'] as const;

const SEVERITY_COLORS: Record<string, string> = {
  severe: 'bg-red-500/20 text-red-400',
  harsh: 'bg-orange-500/20 text-orange-400',
  moderate: 'bg-yellow-500/20 text-yellow-400',
  mild: 'bg-gray-500/20 text-gray-400',
};

const TYPE_ICONS: Record<string, string> = {
  crash: '💥',
  braking: '🛑',
  harshTurn: '🔄',
  laneDeparture: '↔️',
  followingDistance: '🚗',
  rollingStop: '↩️',
  acceleration: '🏎️',
};

function SeverityBadge({ severity }: { severity: string }) {
  const cls = SEVERITY_COLORS[severity] || SEVERITY_COLORS.mild;
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium capitalize ${cls}`}>{severity}</span>;
}

const columns: AnyColumn[] = [
  {
    key: 'event_type',
    label: 'Event',
    render: (v) => (
      <span>{TYPE_ICONS[v as string] || '⚠️'} <span className="capitalize">{(v as string).replace(/([A-Z])/g, ' $1').trim()}</span></span>
    ),
  },
  {
    key: 'severity',
    label: 'Severity',
    render: (v) => <SeverityBadge severity={v as string} />,
  },
  { key: 'driver_name', label: 'Driver' },
  { key: 'vehicle_name', label: 'Vehicle' },
  {
    key: 'g_force',
    label: 'G-Force',
    render: (v) => {
      const g = v as number;
      return g > 0 ? `${g}g` : '—';
    },
  },
  {
    key: 'time',
    label: 'Time',
    render: (v) => v ? new Date(v as string).toLocaleString() : '—',
  },
  {
    key: 'video_url',
    label: 'Video',
    render: (v) =>
      v ? (
        <a href={v as string} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">
          View
        </a>
      ) : (
        <span className="text-gray-600">—</span>
      ),
  },
];

export default function Events() {
  const [events, setEvents] = useState<SafetyEvent[]>([]);
  const [summary, setSummary] = useState<EventsSummary>({ by_type: {}, by_severity: {} });
  const [days, setDays] = useState(7);
  const [typeFilter, setTypeFilter] = useState('all');
  const [driverSearch, setDriverSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams({ days: String(days) });
    if (typeFilter !== 'all') params.set('event_type', typeFilter);
    if (driverSearch) params.set('driver', driverSearch);

    apiJSON<SafetyEventsResponse>(`/safety/events?${params}`)
      .then((d) => {
        setEvents(d.events || []);
        setSummary(d.summary || { by_type: {}, by_severity: {} });
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, [days, typeFilter, driverSearch]);

  if (error && events.length === 0) return <p className="text-red-400">{error}</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Safety Events</h1>
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
      <div className="grid grid-cols-5 gap-3 mb-6">
        {Object.entries(summary.by_severity).map(([sev, count]) => (
          <div key={sev} className="bg-gray-900 border border-gray-800 rounded-lg p-3">
            <p className="text-xs text-gray-400 capitalize">{sev}</p>
            <p className="text-xl font-bold">{count}</p>
          </div>
        ))}
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3">
          <p className="text-xs text-gray-400">Total</p>
          <p className="text-xl font-bold">{events.length}</p>
        </div>
      </div>

      {/* Event type summary bar */}
      {Object.keys(summary.by_type).length > 0 && (
        <div className="flex flex-wrap gap-2 mb-4">
          {Object.entries(summary.by_type)
            .sort(([, a], [, b]) => b - a)
            .map(([t, count]) => (
              <span
                key={t}
                className="bg-gray-800 text-gray-300 px-2.5 py-1 rounded text-xs cursor-pointer hover:bg-gray-700"
                onClick={() => setTypeFilter(t === typeFilter ? 'all' : t)}
              >
                {TYPE_ICONS[t] || '⚠️'} {t.replace(/([A-Z])/g, ' $1').trim()} ({count})
              </span>
            ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="flex gap-1 overflow-x-auto">
          {EVENT_TYPES.map((t) => (
            <button
              key={t}
              onClick={() => setTypeFilter(t)}
              className={`text-xs px-2.5 py-1 rounded capitalize whitespace-nowrap ${
                typeFilter === t ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
              }`}
            >
              {t === 'all' ? 'All' : t.replace(/([A-Z])/g, ' $1').trim()}
            </button>
          ))}
        </div>
        <input
          type="text"
          placeholder="Filter by driver..."
          value={driverSearch}
          onChange={(e) => setDriverSearch(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded px-2.5 py-1 text-sm placeholder-gray-500 focus:outline-none focus:border-blue-500 w-48"
        />
      </div>

      {loading ? (
        <p className="text-gray-500">Loading...</p>
      ) : (
        <DataTable
          columns={columns}
          data={events as unknown as Record<string, unknown>[]}
          searchKey="driver_name"
        />
      )}

      <p className="text-xs text-gray-500 mt-2">{events.length} event{events.length !== 1 ? 's' : ''}</p>
    </div>
  );
}
