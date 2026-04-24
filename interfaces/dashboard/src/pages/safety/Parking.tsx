import { useEffect, useState, useCallback } from 'react';
import { apiJSON, apiFetch } from '../../api/client';
import DataTable from '../../components/DataTable';
import type { ParkingEvent, ParkingEventsResponse, AnyColumn } from '../../types';

/* ── Badge helpers ─────────────────────────────────────────── */

function ClassBadge({ cls }: { cls: string }) {
  const colors: Record<string, string> = {
    safe:     'bg-green-500/15 text-green-700 dark:text-green-400',
    geofence: 'bg-green-500/15 text-green-700 dark:text-green-400',
    unsafe:   'bg-red-500/15 text-red-700 dark:text-red-400',
    unknown:  'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400',
  };
  const c = colors[cls] || 'bg-gray-500/20 text-muted-foreground';
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium uppercase ${c}`}>{cls}</span>;
}

function AlertBadge({ level }: { level: string }) {
  const colors: Record<string, string> = {
    critical:  'bg-red-500/15 text-red-700 dark:text-red-400',
    warning:   'bg-orange-500/15 text-orange-700 dark:text-orange-400',
    breakdown: 'bg-purple-500/15 text-purple-700 dark:text-purple-400',
    none:      'bg-gray-500/20 text-muted-foreground',
  };
  const c = colors[level] || 'bg-gray-500/20 text-muted-foreground';
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium uppercase ${c}`}>{level}</span>;
}

function formatDuration(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 24) return `${hours.toFixed(1)}h`;
  const d = Math.floor(hours / 24);
  const h = Math.round(hours % 24);
  return `${d}d ${h}h`;
}

function mapsUrl(lat: number, lng: number): string {
  return `https://www.google.com/maps?q=${lat},${lng}`;
}

/* ── History table columns ─────────────────────────────────── */

const historyColumns: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle' },
  { key: 'company_code', label: 'Company' },
  {
    key: 'location_class',
    label: 'Classification',
    render: (v) => <ClassBadge cls={v as string} />,
  },
  {
    key: 'alert_level',
    label: 'Alert Level',
    render: (v) => <AlertBadge level={v as string} />,
  },
  { key: 'address', label: 'Address' },
  {
    key: 'duration_hours',
    label: 'Duration',
    render: (v) => formatDuration(v as number),
  },
  {
    key: 'first_stopped',
    label: 'Stopped At',
    render: (v) => v ? new Date(v as string).toLocaleString() : '—',
  },
  {
    key: 'last_checked',
    label: 'Resolved',
    render: (v) => v ? new Date(v as string).toLocaleString() : '—',
  },
];

/* ── Main Component ────────────────────────────────────────── */

export default function Parking() {
  const [tab, setTab] = useState<'active' | 'history'>('active');
  const [events, setEvents] = useState<ParkingEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [vehicleSearch, setVehicleSearch] = useState('');
  const [classFilter, setClassFilter] = useState('all');
  const [showAll, setShowAll] = useState(false);
  const [days, setDays] = useState(7);
  const [resolving, setResolving] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [mapUrls, setMapUrls] = useState<Record<number, string>>({});

  const fetchEvents = useCallback(() => {
    setLoading(true);
    setError('');
    const params = new URLSearchParams();
    if (vehicleSearch) params.set('vehicle', vehicleSearch);

    if (tab === 'active') {
      if (showAll) params.set('attention_only', 'false');
      const qs = params.toString();
      apiJSON<ParkingEventsResponse>(`/parking/active${qs ? `?${qs}` : ''}`)
        .then((d) => setEvents(d.events || []))
        .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
        .finally(() => setLoading(false));
    } else {
      params.set('days', String(days));
      if (classFilter !== 'all') params.set('location_class', classFilter);
      const qs = params.toString();
      apiJSON<ParkingEventsResponse>(`/parking/history${qs ? `?${qs}` : ''}`)
        .then((d) => setEvents(d.events || []))
        .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
        .finally(() => setLoading(false));
    }
  }, [tab, vehicleSearch, showAll, days, classFilter]);

  useEffect(() => { fetchEvents(); }, [fetchEvents]);

  async function resolveEvent(event: ParkingEvent) {
    setResolving(event.id);
    try {
      await apiJSON(`/parking/${event.id}/resolve`, { method: 'POST' });
      setEvents((prev) => prev.filter((e) => e.id !== event.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Resolve failed');
    } finally {
      setResolving(null);
    }
  }

  function toggleExpand(ev: ParkingEvent) {
    const id = ev.id;
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    // Fetch map image on first expand (authenticated)
    if (!mapUrls[id] && ev.map_image_path) {
      apiFetch(`/parking/${id}/map-image`)
        .then((res) => res.ok ? res.blob() : null)
        .then((blob) => {
          if (blob) {
            setMapUrls((prev) => ({ ...prev, [id]: URL.createObjectURL(blob) }));
          }
        })
        .catch(() => {});
    }
  }

  if (error && events.length === 0) return <p className="text-destructive">{error}</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Parking</h1>
        <button
          onClick={fetchEvents}
          className="text-sm text-muted-foreground hover:text-foreground transition"
        >
          ↻ Refresh
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4">
        {(['active', 'history'] as const).map((t) => (
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

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <input
          type="text"
          placeholder="Filter by vehicle..."
          value={vehicleSearch}
          onChange={(e) => setVehicleSearch(e.target.value)}
          className="bg-muted border border-border rounded px-2.5 py-1 text-sm placeholder-muted-foreground focus:outline-none focus:border-ring w-48"
        />
        {tab === 'active' && (
          <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
            <input
              type="checkbox"
              checked={showAll}
              onChange={(e) => setShowAll(e.target.checked)}
              className="rounded bg-muted border-border"
            />
            Show safe locations
          </label>
        )}
        {tab === 'history' && (
          <>
            <div className="flex gap-1">
              {['all', 'safe', 'unsafe', 'unknown'].map((c) => (
                <button
                  key={c}
                  onClick={() => setClassFilter(c)}
                  className={`text-xs px-2.5 py-1 rounded capitalize ${
                    classFilter === c ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:bg-muted/80'
                  }`}
                >
                  {c}
                </button>
              ))}
            </div>
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="bg-muted border border-border rounded px-2 py-1 text-sm text-foreground/80"
            >
              {[7, 14, 30, 60, 90].map((d) => (
                <option key={d} value={d}>{d} days</option>
              ))}
            </select>
          </>
        )}
      </div>

      {/* Content */}
      {loading && events.length === 0 ? (
        <p className="text-muted-foreground">Loading...</p>
      ) : tab === 'active' ? (
        events.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">
            <p className="text-lg mb-1">No active parking events</p>
            <p className="text-sm">All vehicles are parked in safe locations or moving</p>
          </div>
        ) : (
          <div className="space-y-3">
            {events.map((ev) => (
              <div key={ev.id} className="bg-card border border-border rounded-xl p-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3 mb-2">
                      <span className="font-semibold text-foreground">{ev.vehicle_name}</span>
                      <span className="text-xs text-muted-foreground">{ev.company_code}</span>
                      <ClassBadge cls={ev.location_class} />
                      <AlertBadge level={ev.alert_level} />
                    </div>
                    <div className="text-sm text-muted-foreground space-y-1">
                      <p>
                        <span className="text-muted-foreground">Address:</span>{' '}
                        {ev.address ? (
                          <a
                            href={mapsUrl(ev.latitude, ev.longitude)}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-primary hover:underline"
                          >
                            {ev.address}
                          </a>
                        ) : (
                          <a
                            href={mapsUrl(ev.latitude, ev.longitude)}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-primary hover:underline"
                          >
                            {ev.latitude.toFixed(5)}, {ev.longitude.toFixed(5)}
                          </a>
                        )}
                      </p>
                      <p>
                        <span className="text-muted-foreground">Duration:</span>{' '}
                        <span className={ev.duration_hours >= 8 ? 'text-red-600 dark:text-red-400 font-medium' : ev.duration_hours >= 2 ? 'text-orange-600 dark:text-orange-400' : ''}>
                          {formatDuration(ev.duration_hours)}
                        </span>
                        <span className="text-muted-foreground ml-2">
                          (since {new Date(ev.first_stopped).toLocaleString()})
                        </span>
                      </p>
                    </div>
                    {/* Expandable AI Analysis + Map Image */}
                    {(ev.ai_analysis || ev.map_image_path) && (
                      <div className="mt-2">
                        <button
                          onClick={() => toggleExpand(ev)}
                          className="text-xs text-primary hover:text-primary/80 transition"
                        >
                          {expanded.has(ev.id) ? '▼ Hide AI Analysis' : '▶ Show AI Analysis'}
                        </button>
                        {expanded.has(ev.id) && (
                          <div className="mt-2 space-y-3">
                            {ev.map_image_path && (
                              <div>
                                <p className="text-xs text-muted-foreground mb-1">Satellite + Road Map (analyzed by AI):</p>
                                {mapUrls[ev.id] ? (
                                  <img
                                    src={mapUrls[ev.id]}
                                    alt={`Parking map for ${ev.vehicle_name}`}
                                    className="rounded-lg border border-border max-w-full"
                                    style={{ maxHeight: '300px' }}
                                  />
                                ) : (
                                  <p className="text-xs text-muted-foreground">Loading map...</p>
                                )}
                              </div>
                            )}
                            {ev.ai_analysis && (
                              <pre className="text-xs text-muted-foreground bg-muted rounded p-3 whitespace-pre-wrap max-h-40 overflow-y-auto">
                                {ev.ai_analysis}
                              </pre>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="flex flex-col items-end gap-2 ml-4 shrink-0">
                    <button
                      onClick={() => resolveEvent(ev)}
                      disabled={resolving === ev.id}
                      className="px-3 py-1.5 bg-green-600 hover:bg-green-700 disabled:opacity-50 rounded-lg text-xs font-medium text-foreground transition"
                    >
                      {resolving === ev.id ? 'Resolving...' : 'Resolve'}
                    </button>
                    <a
                      href={mapsUrl(ev.latitude, ev.longitude)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-lg text-xs font-medium transition"
                    >
                      📍 Map
                    </a>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )
      ) : (
        <DataTable
          columns={historyColumns}
          data={events as unknown as Record<string, unknown>[]}
          searchKey="vehicle_name"
        />
      )}

      <p className="text-xs text-muted-foreground mt-3">
        {events.length} event{events.length !== 1 ? 's' : ''}
      </p>
    </div>
  );
}
