import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ParkingSquare } from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import DataGrid from '../../components/datagrid';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
  LastUpdated,
  FilterBar,
  FilterChips,
  DateRangePresets,
} from '../../components/shell';
import type { ParkingEvent, ParkingEventsResponse, AnyColumn } from '../../types';
import { toneClasses, type Tone } from '../../lib/status';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';

/* ── Badge helpers ─────────────────────────────────────────── */

// Parking classification → tone: safe/geofence read as good (ok),
// unsafe is the danger signal, unknown carries no signal (neutral).
const CLASS_TONE: Record<string, Tone> = {
  safe:     'ok',
  geofence: 'ok',
  unsafe:   'danger',
  unknown:  'neutral',
};

function ClassBadge({ cls }: { cls: string }) {
  const c = toneClasses(CLASS_TONE[cls] ?? 'neutral');
  return <span className={`px-2 py-0.5 rounded-md text-xs font-medium uppercase ${c}`}>{cls}</span>;
}

// Alert level → tone.  ``breakdown`` is a distinct categorical state
// (mechanical failure, not a severity step) so it keeps its own
// purple hue; the rest map onto the severity tones.
function AlertBadge({ level }: { level: string }) {
  if (level === 'breakdown') {
    return <span className="px-2 py-0.5 rounded-full text-xs font-medium uppercase bg-purple-500/15 text-purple-700 dark:text-purple-400">{level}</span>;
  }
  const tone: Tone = level === 'critical' ? 'danger' : level === 'warning' ? 'warn' : 'neutral';
  return <span className={`px-2 py-0.5 rounded-md text-xs font-medium uppercase ${toneClasses(tone)}`}>{level}</span>;
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

const titleCaseCls = (s: string) =>
  s ? s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : '(none)';

function makeHistoryColumns(tz: string): AnyColumn[] {
  return [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true, filterable: true },
  { key: 'company_code', label: 'Company', sortable: true, filterable: true },
  {
    key: 'location_class',
    label: 'Classification',
    sortable: true,
    filterable: true,
    filterValue: (row) => String((row as { location_class?: string }).location_class ?? ''),
    filterLabel: (row) => titleCaseCls(String((row as { location_class?: string }).location_class ?? '')),
    render: (v) => <ClassBadge cls={v as string} />,
  },
  {
    key: 'alert_level',
    label: 'Alert Level',
    sortable: true,
    filterable: true,
    filterValue: (row) => String((row as { alert_level?: string }).alert_level ?? ''),
    filterLabel: (row) => titleCaseCls(String((row as { alert_level?: string }).alert_level ?? '')),
    render: (v) => <AlertBadge level={v as string} />,
  },
  { key: 'address', label: 'Address' },
  {
    key: 'duration_hours',
    label: 'Duration',
    sortable: true,
    filterable: true, filterMode: 'range', filterRange: { min: 0, step: 1, unit: 'h' },
    render: (v) => formatDuration(v as number),
  },
  {
    key: 'first_stopped',
    label: 'Stopped At',
    sortable: true,
    filterable: true, filterMode: 'date-range',
    render: (v) => v ? formatDate(v as string, { timeZone: tz }) : '—',
  },
  {
    key: 'last_checked',
    label: 'Resolved',
    render: (v) => v ? formatDate(v as string, { timeZone: tz }) : '—',
  },
  ];
}

/* ── Main Component ────────────────────────────────────────── */

export default function Parking() {
  const { t } = useTranslation();
  const tz = useTimezone();
  const qc = useQueryClient();
  const [tab, setTab] = useState<'active' | 'history'>('active');
  const [error, setError] = useState('');
  const [vehicleSearch, setVehicleSearch] = useState('');
  const [classFilter, setClassFilter] = useState('all');
  const [showAll, setShowAll] = useState(false);
  const [days, setDays] = useState(30);
  const [resolving, setResolving] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [mapUrls, setMapUrls] = useState<Record<number, string>>({});
  const [mapErrors, setMapErrors] = useState<Record<number, string>>({});

  const queryKey = ['parking', tab, vehicleSearch, tab === 'active' ? showAll : null, tab === 'history' ? days : null, tab === 'history' ? classFilter : null] as const;
  const { data, isLoading: loading, isFetching, error: queryError, refetch, dataUpdatedAt } = useQuery<ParkingEventsResponse>({
    queryKey,
    queryFn: () => {
      const params = new URLSearchParams();
      if (vehicleSearch) params.set('vehicle', vehicleSearch);
      if (tab === 'active') {
        if (showAll) params.set('attention_only', 'false');
        const qs = params.toString();
        return apiJSON<ParkingEventsResponse>(`/parking/active${qs ? `?${qs}` : ''}`);
      }
      params.set('days', String(days));
      if (classFilter !== 'all') params.set('location_class', classFilter);
      const qs = params.toString();
      return apiJSON<ParkingEventsResponse>(`/parking/history${qs ? `?${qs}` : ''}`);
    },
    placeholderData: (prev) => prev,
  });
  const events: ParkingEvent[] = data?.events ?? [];
  const fetchError = queryError instanceof Error ? queryError.message : '';

  async function resolveEvent(event: ParkingEvent) {
    setResolving(event.id);
    try {
      await apiJSON(`/parking/${event.id}/resolve`, { method: 'POST' });
      qc.invalidateQueries({ queryKey: ['parking'] });
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
    // Fetch map image on first expand (authenticated).
    //
    // A failure MUST become visible state.  This used to map a non-OK
    // response to null and drop it, so the panel sat on "Loading map..."
    // for good — which is exactly how a 404 on every event went
    // unnoticed until the browser console was opened.  A spinner that
    // never resolves reads as "slow", not "broken".
    if (!mapUrls[id] && !mapErrors[id] && ev.map_image_path) {
      apiFetch(`/parking/${id}/map-image`)
        .then(async (res) => {
          if (!res.ok) {
            setMapErrors((prev) => ({
              ...prev,
              [id]: res.status === 404
                ? 'Map image unavailable for this event.'
                : `Could not load map (HTTP ${res.status}).`,
            }));
            return;
          }
          const blob = await res.blob();
          setMapUrls((prev) => ({ ...prev, [id]: URL.createObjectURL(blob) }));
        })
        .catch(() => {
          setMapErrors((prev) => ({ ...prev, [id]: 'Could not load map — network error.' }));
        });
    }
  }

  const displayError = error || fetchError;

  return (
    <div>
      <PageHeader
        icon={ParkingSquare}
        title={t('pages.parking_title')}
        description={
          tab === 'active'
            ? 'Vehicles currently parked. Resolve events when drivers move on, or open the AI analysis to see why a stop was flagged.'
            : 'Past parking stops. Filter by classification to find unsafe parking patterns over time.'
        }
        actions={
          <LastUpdated
            fetchedAt={dataUpdatedAt}
            isFetching={isFetching}
            onRefresh={refetch}
          />
        }
      />

      <div className="flex gap-1 mb-4 border-b border-border">
        {(['active', 'history'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium transition capitalize border-b-2 -mb-px ${
              tab === t
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      <FilterBar>
        <input
          type="text"
          placeholder={t('forms.vehicle_name_placeholder')}
          value={vehicleSearch}
          onChange={(e) => setVehicleSearch(e.target.value)}
          className="bg-background border border-border rounded-md px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:border-ring w-44"
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
            <FilterChips
              options={['all', 'safe', 'unsafe', 'unknown'] as const}
              value={classFilter as 'all' | 'safe' | 'unsafe' | 'unknown'}
              onChange={(v) => setClassFilter(v)}
            />
            <DateRangePresets value={days} onChange={setDays} isFetching={isFetching} />
          </>
        )}
      </FilterBar>

      {displayError && events.length === 0 ? (
        <ErrorState
          title="Couldn't load parking events"
          message={displayError}
          onRetry={() => refetch()}
        />
      ) : loading && events.length === 0 ? (
        <TableSkeleton rows={6} cols={5} />
      ) : tab === 'active' ? (
        events.length === 0 ? (
          <EmptyState
            icon={ParkingSquare}
            title="No active parking events"
            description="All vehicles are parked in safe locations or are currently moving."
          />
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
                        <span className={ev.duration_hours >= 8 ? 'text-danger font-medium' : ev.duration_hours >= 2 ? 'text-warn' : ''}>
                          {formatDuration(ev.duration_hours)}
                        </span>
                        <span className="text-muted-foreground ml-2">
                          (since {formatDate(ev.first_stopped, { timeZone: tz })})
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
                                ) : mapErrors[ev.id] ? (
                                  <p className="text-xs text-destructive">{mapErrors[ev.id]}</p>
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
                      className="px-3 py-1.5 bg-ok hover:bg-ok/90 disabled:opacity-50 rounded-lg text-xs font-medium text-foreground transition"
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
        <DataGrid
          tableId="parking-history"
          columns={makeHistoryColumns(tz)}
          data={events as unknown as Record<string, unknown>[]}
          searchKey={['vehicle_name', 'address']}
        />
      )}

      <p className="text-xs text-muted-foreground mt-3">
        {events.length} event{events.length !== 1 ? 's' : ''}
      </p>
    </div>
  );
}
