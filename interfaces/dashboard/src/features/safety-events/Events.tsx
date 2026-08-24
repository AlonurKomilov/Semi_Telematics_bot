import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import type { ElementType } from 'react';
import { AlertTriangle, Zap, RotateCcw, MoveHorizontal, Truck, OctagonX, TrendingUp, Play } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { formatDate } from '../../utils/datetime';
import { useTimezone } from '../../hooks/useTimezone';
import { toneClasses, type Tone } from '../../lib/status';
import DataGrid from '../../components/datagrid';
import EventVideoModal from '@/features/safety-events/EventVideoModal';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
  useLoadingStage,
  DateRangePresets,
} from '../../components/shell';
import type { SafetyEvent, SafetyEventsResponse, AnyColumn } from '../../types';
import { iconSizeClass } from '@/lib/iconSize';
import { Card } from '@/components/ui/card';

const EVENT_TYPES = ['all', 'crash', 'braking', 'harshTurn', 'laneDeparture', 'followingDistance', 'rollingStop', 'acceleration'] as const;

// Samsara's severity vocabulary (severe/harsh/moderate/mild) → semantic
// tone.  These strings are event-grader-specific so they aren't in the
// shared statusTone map; map them to a Tone here and let toneClasses
// render the soft-pill.
const SEVERITY_TONE: Record<string, Tone> = {
  severe: 'danger',
  harsh: 'warn',
  moderate: 'warn',
  mild: 'neutral',
};

type EventIconKey = 'crash' | 'braking' | 'harshTurn' | 'laneDeparture' | 'followingDistance' | 'rollingStop' | 'acceleration';

const TYPE_ICON_COMPONENTS: Record<EventIconKey, ElementType> = {
  crash:           AlertTriangle,
  braking:         OctagonX,
  harshTurn:       RotateCcw,
  laneDeparture:   MoveHorizontal,
  followingDistance: Truck,
  rollingStop:     Zap,
  acceleration:    TrendingUp,
};

const TYPE_ICON_COLORS: Record<EventIconKey, string> = {
  crash:           'text-red-500',
  braking:         'text-orange-500',
  harshTurn:       'text-yellow-500',
  laneDeparture:   'text-blue-500',
  followingDistance: 'text-purple-500',
  rollingStop:     'text-cyan-500',
  acceleration:    'text-green-500',
};

function EventIcon({ type, size = 14 }: { type: string; size?: number }) {
  const Icon = TYPE_ICON_COMPONENTS[type as EventIconKey] ?? AlertTriangle;
  const color = TYPE_ICON_COLORS[type as EventIconKey] ?? 'text-muted-foreground';
  return <Icon className={`inline-block shrink-0 ${iconSizeClass(size)} ${color}`} />;
}

function SeverityBadge({ severity }: { severity: string }) {
  const cls = toneClasses(SEVERITY_TONE[severity] ?? 'neutral');
  return <span className={`px-2 py-0.5 rounded-md text-xs font-medium capitalize ${cls}`}>{severity}</span>;
}

// Strip Samsara's accusatory suffix words from a behavior label so the
// table shows "Edge Railroad Crossing" instead of "Edge Railroad
// Crossing Violation".  The event itself is unambiguous; the trailing
// label adds visual noise and reads as punitive.
const NOISE_SUFFIX_RE = /\s+(?:Violation|Detected|Detection|Event)$/i;
function cleanEventLabel(raw: string): string {
  // camelCase → spaced words, then strip suffix repeatedly.
  let label = raw.replace(/([A-Z])/g, ' $1').trim();
  for (let i = 0; i < 4 && NOISE_SUFFIX_RE.test(label); i++) {
    label = label.replace(NOISE_SUFFIX_RE, '').trim();
  }
  return label;
}

// Columns shared across renders.  The Video column is appended inside
// the component because it needs the modal-open callback from state.
const baseColumns: AnyColumn[] = [
  {
    key: 'event_type',
    label: 'Event',
    sortable: true,
    // Event types are a small enum (harsh brake / crash / rolling
    // stop / …) — filter matches the raw key, dropdown shows the same
    // cleaned label the cell renders.
    filterable: true,
    filterValue: (row) => String((row as SafetyEvent).event_type ?? ''),
    filterLabel: (row) => cleanEventLabel(String((row as SafetyEvent).event_type ?? '')),
    render: (v) => (
      <span className="flex items-center gap-1.5">
        <EventIcon type={v as string} />
        <span className="capitalize">{cleanEventLabel(v as string)}</span>
      </span>
    ),
  },
  {
    key: 'severity',
    label: 'Severity',
    sortable: true,
    filterable: true,
    filterValue: (row) => String((row as SafetyEvent).severity ?? ''),
    filterLabel: (row) => {
      const s = String((row as SafetyEvent).severity ?? '').toLowerCase();
      return s ? s.charAt(0).toUpperCase() + s.slice(1) : '(none)';
    },
    render: (v) => <SeverityBadge severity={v as string} />,
  },
  { key: 'driver_name', label: 'Driver', sortable: true, filterable: true },
  { key: 'vehicle_name', label: 'Vehicle', sortable: true, filterable: true },
  {
    key: 'g_force',
    label: 'G-Force',
    sortable: true,
    filterable: true,
    filterMode: 'range',
    filterRange: { min: 0, step: 0.1, unit: 'g' },
    render: (v) => {
      const g = v as number;
      return g > 0 ? `${g}g` : '—';
    },
  },
];

export default function Events() {
  const { t } = useTranslation();
  const tz = useTimezone();
  // Default 30 days so the page lands with a full month of context;
  // matches the Scorecards / Risk Summary defaults.  Users still get
  // the 7 / 14 / 60 / 90 presets in the DateRangePresets menu.
  const [days, setDays] = useState(30);
  // Explicit window END day (YYYY-MM-DD, null = today).  /safety/events
  // honors ``end=`` — this page unlocks the picker's two-click range
  // mode ("show me the week of the incident", not just "last N days").
  const [end, setEnd] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState('all');
  const [driverSearch, setDriverSearch] = useState('');
  // Inline video viewer state — null means closed.  Opening surfaces
  // the Samsara-style player with vehicle/driver/location chrome plus
  // a Download fallback.
  const [viewingEvent, setViewingEvent] = useState<SafetyEvent | null>(null);

  // Compose the Video column inside the component so its render can
  // reach `setViewingEvent`.  Memoised so DataGrid doesn't see a new
  // columns array on every keystroke in the driver filter.
  const columns: AnyColumn[] = useMemo(() => [
    ...baseColumns,
    {
      key: 'time',
      label: 'Time',
      sortable: true,
      filterable: true,
      filterMode: 'date-range',
      render: (v) => v ? formatDate(v as string, { timeZone: tz }) : '—',
    },
    {
      key: 'video_url',
      label: 'Video',
      // Open the modal viewer instead of a raw S3 link.  Direct
      // links 403 after the 8-hour pre-sign window expires and force
      // a download instead of inline playback; the modal hits the
      // /api/safety/events/{id}/video proxy on every load so the URL
      // is always fresh.
      render: (v, row) => {
        const hasVideo = !!v;
        const evt = row as SafetyEvent;
        if (!hasVideo || !evt.event_id) {
          return <span className="text-muted-foreground">—</span>;
        }
        return (
          <button
            type="button"
            onClick={() => setViewingEvent(evt)}
            className="inline-flex items-center gap-1 text-primary hover:underline text-sm py-1 -my-1 min-h-tap"
          >
            <Play className="size-3" />
            View
          </button>
        );
      },
    },
  ], [tz]);

 // React Query: cache events per (days, type, driver) tuple.
  // staleTime=60s — events ingest every 5 min so refetching on every
  // mount (default ``staleTime=0``) just adds latency.  Within the
  // minute, a remount uses the cached array; after that, React Query
  // refetches in the background (placeholderData keeps the previous
  // result on screen during the refetch).
  const { data, isLoading, isFetching, error: queryError, refetch } = useQuery<SafetyEventsResponse>({
    queryKey: ['safety-events', days, end, typeFilter, driverSearch],
    queryFn: () => {
      const params = new URLSearchParams({ days: String(days) });
      if (end) params.set('end', end);
      if (typeFilter !== 'all') params.set('event_type', typeFilter);
      if (driverSearch) params.set('driver', driverSearch);
      return apiJSON<SafetyEventsResponse>(`/safety/events?${params}`);
    },
    placeholderData: (prev) => prev,
    staleTime: 60_000,
  });

  const events  = data?.events ?? [];
  const summary = data?.summary ?? { by_type: {}, by_severity: {} };
  const loading = isLoading && !data;
  const error   = queryError instanceof Error ? queryError.message : (queryError ? 'Failed to load' : '');

  // Loading stages — events fall back to live Samsara when the warehouse
  // is cold, which can take 10-30+ seconds.  Without progressive
  // feedback the page reads as "frozen."  See useLoadingStage docs.
  const stage = useLoadingStage(loading);

  // Hard timeout: once we cross 30s with no response, give the user a
  // Retry button instead of an indefinite skeleton.
  if (stage === 'timeout' && events.length === 0) {
    return (
      <div>
        <PageHeader
          icon={AlertTriangle}
          title={t('events.page_title')}
          description={t('events.page_description')}
        />
        <ErrorState
          title={t('common.loading_takes_long')}
          message={t('scorecards.loading_too_long_message')}
          onRetry={() => refetch()}
        />
      </div>
    );
  }

  if (error && events.length === 0) {
    return (
      <div>
        <PageHeader
          icon={AlertTriangle}
          title={t('events.page_title')}
          description={t('events.page_description')}
        />
        <ErrorState message={error} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        icon={AlertTriangle}
        title={t('events.page_title')}
        description={t('events.page_description_long')}
        actions={
          <DateRangePresets
            value={days}
            end={end}
            onChange={(d) => { setDays(d); setEnd(null); }}
            onApplyRange={(d, e) => { setDays(d); setEnd(e); }}
            isFetching={isFetching}
          />
        }
      />

      {/* Summary cards */}
      <div className="grid grid-cols-5 gap-3 mb-6">
        {Object.entries(summary.by_severity).map(([sev, count]) => (
          <Card padding="compact" key={sev}>
            <p className="text-xs text-muted-foreground capitalize">{sev}</p>
            <p className="text-xl font-bold">{count}</p>
          </Card>
        ))}
        <Card padding="compact">
          <p className="text-xs text-muted-foreground">Total</p>
          <p className="text-xl font-bold">{events.length}</p>
        </Card>
      </div>

      {/* Event type summary bar */}
      {Object.keys(summary.by_type).length > 0 && (
        <div className="flex flex-wrap gap-2 mb-4">
          {Object.entries(summary.by_type)
            .sort(([, a], [, b]) => b - a)
            .map(([t, count]) => (
              <span
                key={t}
                className="inline-flex items-center gap-1.5 bg-muted text-foreground/80 px-2.5 py-1 rounded text-xs cursor-pointer hover:bg-muted/80 min-h-tap"
                onClick={() => setTypeFilter(t === typeFilter ? 'all' : t)}
              >
                <EventIcon type={t} size={12} />
                {cleanEventLabel(t)} ({count})
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
                typeFilter === t ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:bg-muted/80'
              } min-h-tap`}
            >
              {t === 'all' ? 'All' : cleanEventLabel(t)}
            </button>
          ))}
        </div>
        <input
          type="text"
          placeholder="Filter by driver..."
          value={driverSearch}
          onChange={(e) => setDriverSearch(e.target.value)}
          className="bg-muted border border-border rounded px-2.5 py-1 text-sm placeholder-muted-foreground focus:outline-none focus:border-ring w-48"
        />
      </div>

      {loading ? (
        <TableSkeleton
          rows={6}
          cols={5}
          message={
            stage === 'slow'
              ? 'Still loading… Samsara may be slow.'
              : 'Loading safety events…'
          }
        />
      ) : events.length === 0 ? (
        <EmptyState
          icon={AlertTriangle}
          title={typeFilter !== 'all' || driverSearch ? 'No events match your filter' : 'No safety events in this window'}
          description={
            typeFilter !== 'all' || driverSearch
              ? 'Clear filters or widen the date range.'
              : 'Try widening the date range — events accrue as drivers operate.'
          }
        />
      ) : (
        <DataGrid
          // A failed REFETCH leaves these rows on screen, so the
          // ``length === 0`` branch above never fires and the operator
          // reads a stale table as current.  The band says so without
          // taking the rows or the controls away.
          error={error || undefined}
          onRetry={() => refetch()}
          tableId="safety-events"
          columns={columns}
          data={events as unknown as Record<string, unknown>[]}
          searchKey={['driver_name', 'vehicle_name']}
        />
      )}

      {events.length > 0 && (
        <p className="text-xs text-muted-foreground mt-2">{events.length} event{events.length !== 1 ? 's' : ''}</p>
      )}

      {/* Inline video viewer — rendered at the page root so the modal
          backdrop covers the full viewport.  Mounted only while an
          event is selected to avoid keeping a hidden <video> in the
          DOM (memory + battery on long sessions). */}
      {viewingEvent && (
        <EventVideoModal
          event={viewingEvent}
          onClose={() => setViewingEvent(null)}
        />
      )}
    </div>
  );
}
