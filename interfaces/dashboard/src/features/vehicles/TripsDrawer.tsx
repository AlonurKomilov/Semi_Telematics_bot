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
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetBody, SheetClose,
} from '../../components/ui/sheet';
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

/** Human length of the drawer's window — same convention as the range
 *  picker's footer: timed ranges show true duration ("7 days 2h"),
 *  whole-day ranges the inclusive calendar count ("8 days"). */
function windowLabel(start: string, end: string): string {
  const [sDay, sTime] = start.split('T');
  const [eDay, eTime] = end.split('T');
  const dayMs = new Date(`${eDay}T00:00:00`).getTime()
    - new Date(`${sDay}T00:00:00`).getTime();
  const days = Math.round(dayMs / 86_400_000);
  if (!sTime && !eTime) return `${days + 1} days`;
  const toMin = (t?: string) => {
    if (!t) return 0;
    const [h, m] = t.split(':').map(Number);
    return h * 60 + m;
  };
  const totalMin = days * 24 * 60 - toMin(sTime)
    + (eTime ? toMin(eTime) : 24 * 60);
  const d = Math.floor(totalMin / (24 * 60));
  const h = Math.round((totalMin % (24 * 60)) / 60);
  return `${d} day${d === 1 ? '' : 's'}${h ? ` ${h}h` : ''}`;
}

export default function TripsDrawer({
  vehicleName, company = '', rowMiles, rowFlag = '', rowCoversWindow = true,
  start, end, onClose,
}: {
  vehicleName: string;
  /** The row's company code — unit numbers repeat across companies
   *  ("103" is a real truck in both G1 and OSY), so the fetch must
   *  say WHICH one or the server's name match may pick the other. */
  company?: string;
  /** The odometer-delta miles the Mileage row showed — the cross-check. */
  rowMiles: number;
  /** The row's coverage flag — echoed here so the warning travels with
   *  the drill-in instead of staying behind on the grid. */
  rowFlag?: string;
  /** False when times-of-day were requested but this row's odometer
   *  delta fell back to whole days.  Trips honor the exact times, so
   *  the two totals then cover DIFFERENT windows — comparing them
   *  produced a false "device jump or swap" alarm. */
  rowCoversWindow?: boolean;
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
    queryKey: ['vehicle-trips', vehicleName, company, start, end],
    queryFn: () => apiJSON<TripsResponse>(
      `/vehicles/${encodeURIComponent(vehicleName)}/trips?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}${company ? `&company=${encodeURIComponent(company)}` : ''}`,
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
  const showsCrossCheck = !isLoading && !error && trips.length > 0 && gap > 1
    && rowCoversWindow;
  const showsWindowNote = !isLoading && !error && trips.length > 0
    && !rowCoversWindow;
  // "Small gaps are normal" was written for the ±3% case and must not
  // crown a jumped odometer as authoritative: production truck 233
  // showed 24,352 odometer miles against 0.6 GPS miles.  Past 25% AND
  // 100 mi, the honest message inverts.
  const gapIsAbsurd = gap > 100
    && gap / Math.max(gpsTotal, rowMiles, 1) > 0.25;

  return (
    // <Sheet> rather than a hand-rolled overlay: the old one was a
    // backdrop <button> plus an <aside role="dialog">, which LOOKED like
    // a modal and behaved like a div — no focus trap, no Escape, no
    // aria-modal, and the page behind it still scrolled.  ``open`` is
    // always true because the parent mounts this component only while the
    // drawer is open; onOpenChange routes Escape and backdrop clicks to
    // the same onClose the ✕ already used.
    <Sheet open onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent
        side="right"
        size="xl"
        aria-label={`Trips for ${vehicleName}`}
        // The header already carries a ✕; the primitive's own would sit
        // on top of it — two close buttons overlapping, one unlabelled.
        showCloseButton={false}
      >
        <SheetHeader className="px-5 py-4 border-b border-border flex-row items-start gap-3 shrink-0">
          <div className="flex-1 min-w-0">
            <SheetTitle className="text-base font-semibold flex items-center gap-2">
              Trips — {vehicleName}{company ? ` · ${company}` : ''}
              {rowFlag && FLAG_NOTE[rowFlag] && (
                <Tip label={FLAG_NOTE[rowFlag].tip}>
                  <span className={`px-2 py-0.5 rounded-md text-xs font-medium ${toneClasses('warn')}`}>
                    {FLAG_NOTE[rowFlag].label}
                  </span>
                </Tip>
              )}
            </SheetTitle>
            <p className="text-xs text-muted-foreground mt-0.5">
              {start.replace('T', ' ')} → {end.replace('T', ' ')}
              {' · '}{windowLabel(start, end)}
              {data && !data.no_data && (
                <>
                  {' · '}{data.trip_count} trip{data.trip_count === 1 ? '' : 's'}
                  {' · '}{gpsTotal.toLocaleString()} mi
                  {' · '}{hhmm(data.driving_min ?? 0)} driving
                </>
              )}
            </p>
          </div>
          <SheetClose
            className="text-muted-foreground hover:text-foreground p-1 -m-1"
            aria-label="Close"
          >
            <X size={16} />
          </SheetClose>
        </SheetHeader>

        {/* SheetBody is a real scroll region (components/scrolling) —
            focusable and named, which a bare overflow div is not. */}
        <SheetBody label="Trips" className="px-5 py-4">
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
          {showsWindowNote && (
            <p className="mt-3 text-xs text-muted-foreground">
              These trips honor your exact times, but the mileage row’s
              odometer total ({rowMiles.toLocaleString()} mi) could only be
              computed in whole days for this range — the two numbers cover
              different windows, so they aren’t directly comparable.
            </p>
          )}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
