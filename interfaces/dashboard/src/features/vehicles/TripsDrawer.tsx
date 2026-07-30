/**
 * Trips drill-in — slides over the Mileage tab when the operator opens
 * a vehicle row.  "How exactly did 132 drive those miles?": one trip
 * per start→stop segment, newest first, for the SAME range the row was
 * computed over (the range travels in as props — re-picking dates
 * inside the drawer would break the question mid-thought).
 *
 * Trips are a LIVE Samsara fetch (on-demand, one vehicle, one range) —
 * unlike the mileage numbers, which come from our stored odometer
 * history.  The error state says exactly that, so a Samsara outage
 * doesn't read as "the mileage numbers are broken too".
 *
 * The odometer-vs-GPS cross-check line is deliberate honesty: summed
 * GPS trip miles never exactly equal the OBD odometer delta; showing
 * both beats letting the user discover the mismatch and distrust the
 * page.
 */
import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { X } from 'lucide-react';

import { apiJSON } from '../../api/client';
import DataGrid from '../../components/datagrid';
import { formatDate } from '../../utils/datetime';
import { useTimezone } from '../../hooks/useTimezone';
import { Tip } from '../../components/tooltip';
import { toneClasses } from '../../lib/status';
import { FLAG_NOTE } from './mileageFlags';

interface TripRow {
  start_ms: number;
  end_ms: number;
  in_progress?: boolean;
  duration_min: number;
  start_location: string;
  end_location: string;
  miles: number;
  driver_id?: number | null;
}

interface TripsResponse {
  start: string;
  end: string;
  vehicle_name: string;
  no_data: boolean;
  reason?: string;
  trips: TripRow[];
  trip_count?: number;
  total_trip_miles?: number;
  driving_min?: number;
}

function hhmm(min: number): string {
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export default function TripsDrawer({
  vehicleName, rowMiles, rowFlag = '', start, end, onClose,
}: {
  vehicleName: string;
  /** The odometer-delta miles the Mileage row showed — the cross-check. */
  rowMiles: number;
  /** The row's coverage flag — echoed here so the warning travels with
   *  the drill-in instead of staying behind on the grid. */
  rowFlag?: string;
  start: string;
  end: string;
  onClose: () => void;
}) {
  const tz = useTimezone();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const { data, isLoading, error } = useQuery<TripsResponse>({
    queryKey: ['vehicle-trips', vehicleName, start, end],
    queryFn: () => apiJSON<TripsResponse>(
      `/vehicles/${encodeURIComponent(vehicleName)}/trips?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
    ),
    staleTime: 5 * 60_000,
    retry: false,
  });

  const trips = data?.trips ?? [];
  const columns = [
    {
      key: 'start_ms', label: 'Start', sortable: true,
      render: (v: unknown) => (
        <span className="text-xs">
          {formatDate(new Date(Number(v)).toISOString(), { timeZone: tz })}
        </span>
      ),
    },
    {
      key: 'duration_min', label: 'Duration', sortable: true,
      render: (v: unknown, row: Record<string, unknown>) => {
        const r = row as unknown as TripRow;
        return (
          <span className="text-xs text-muted-foreground">
            {hhmm(Number(v ?? 0))}
            {r.in_progress && (
              <span className="ml-1 text-ok font-medium">· driving now</span>
            )}
          </span>
        );
      },
    },
    {
      key: 'start_location', label: 'From', sortable: true,
      render: (v: unknown) => (
        <span className="text-xs">{String(v ?? '') || '—'}</span>
      ),
    },
    {
      key: 'end_location', label: 'To', sortable: true,
      render: (v: unknown, row: Record<string, unknown>) => {
        const r = row as unknown as TripRow;
        if (r.in_progress) {
          return <span className="text-xs text-muted-foreground">en route</span>;
        }
        return <span className="text-xs">{String(v ?? '') || '—'}</span>;
      },
    },
    {
      key: 'miles', label: 'Miles', sortable: true,
      render: (v: unknown) => (
        <span className="text-xs font-medium">{Number(v ?? 0).toLocaleString()}</span>
      ),
    },
  ];

  const gpsTotal = data?.total_trip_miles ?? 0;
  const gap = Math.abs(gpsTotal - rowMiles);
  const showsCrossCheck = !isLoading && !error && trips.length > 0 && gap > 1;
  // "Small gaps are normal" was written for the ±3% case and must not
  // crown a jumped odometer as authoritative: production truck 233
  // showed 24,352 odometer miles against 0.6 GPS miles.  Past 25% AND
  // 100 mi, the honest message inverts.
  const gapIsAbsurd = gap > 100
    && gap / Math.max(gpsTotal, rowMiles, 1) > 0.25;

  return (
    <>
      <button
        type="button"
        aria-label="Close trips"
        onClick={onClose}
        className="fixed inset-0 z-40 bg-black/40 cursor-default"
      />
      <aside
        role="dialog"
        aria-label={`Trips for ${vehicleName}`}
        className="fixed top-0 right-0 bottom-0 z-50 w-full sm:max-w-xl bg-background border-l border-border shadow-2xl flex flex-col"
      >
        <header className="px-5 py-4 border-b border-border flex items-start gap-3 shrink-0">
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold flex items-center gap-2">
              Trips — {vehicleName}
              {rowFlag && FLAG_NOTE[rowFlag] && (
                <Tip label={FLAG_NOTE[rowFlag].tip}>
                  <span className={`px-2 py-0.5 rounded-md text-xs font-medium ${toneClasses('warn')}`}>
                    {FLAG_NOTE[rowFlag].label}
                  </span>
                </Tip>
              )}
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              {start.replace('T', ' ')} → {end.replace('T', ' ')}
              {data && !data.no_data && (
                <>
                  {' · '}{data.trip_count} trip{data.trip_count === 1 ? '' : 's'}
                  {' · '}{gpsTotal.toLocaleString()} mi
                  {' · '}{hhmm(data.driving_min ?? 0)} driving
                </>
              )}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground p-1 -m-1"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {isLoading && (
            <p className="text-sm text-muted-foreground">Loading trips from Samsara…</p>
          )}
          {Boolean(error) && !isLoading && (
            <p className="text-sm text-destructive">
              {error instanceof Error ? error.message : 'Trip history fetch failed.'}
            </p>
          )}
          {!isLoading && !error && data?.no_data && (
            <p className="text-sm text-muted-foreground">
              No trip data — this vehicle isn’t telematics-linked, so only
              stored odometer mileage is available.
            </p>
          )}
          {!isLoading && !error && data && !data.no_data && trips.length === 0 && (
            <p className="text-sm text-muted-foreground">
              Samsara reports no trips for this vehicle in the range.
            </p>
          )}
          {trips.length > 0 && (
            <DataGrid
              tableId="vehicle-trips"
              columns={columns}
              data={trips as unknown as Record<string, unknown>[]}
              enableToolbar={false}
            />
          )}
          {showsCrossCheck && !gapIsAbsurd && (
            <p className="mt-3 text-xs text-muted-foreground">
              GPS trip miles ({gpsTotal.toLocaleString()}) and the odometer
              delta ({rowMiles.toLocaleString()}) measure differently — small
              gaps are normal; the odometer number is the authoritative one.
            </p>
          )}
          {showsCrossCheck && gapIsAbsurd && (
            <p className="mt-3 text-xs text-muted-foreground">
              These numbers disagree badly — the odometer delta
              ({rowMiles.toLocaleString()} mi) doesn’t match GPS trips
              ({gpsTotal.toLocaleString()} mi). An odometer device jump or
              swap is the likely cause; for this vehicle the GPS trips
              number is closer to reality.
            </p>
          )}
        </div>
      </aside>
    </>
  );
}
