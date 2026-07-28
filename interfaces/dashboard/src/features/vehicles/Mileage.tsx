/**
 * Period Mileage — "how many miles did each vehicle drive between these
 * dates?" (the Samsara Trip-History question, answered from OUR stored
 * end-of-day odometer history — 730 days, zero live API calls).
 *
 * Rendered as the Vehicles page's "Mileage" page tab.  The number per
 * vehicle is an odometer DELTA (end reading − last reading before the
 * range), never a sum of daily buckets — see get_period_mileage in the
 * warehouse for the rule and the degraded shapes.
 *
 * Honesty rules carried into the UI:
 *   * `partial` / `reset` rows carry a visible coverage note — a
 *     degraded number never looks like a full one.
 *   * Vehicles with NO usable odometer history (manual vehicles,
 *     trailers) are listed by name under the grid — omitted ≠ zero.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Route } from 'lucide-react';

import { apiJSON } from '../../api/client';
import DataGrid from '../../components/datagrid';
import {
  DateRangePresets,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import { Tip } from '../../components/tooltip';
import { toneClasses } from '../../lib/status';
import { useTimezone } from '../../hooks/useTimezone';
import { todayInTimeZone } from '../../utils/datetime';
import TripsDrawer from './TripsDrawer';

interface MileageRow {
  vehicle_id: string;
  vehicle_name: string;
  company: string;
  miles: number;
  start_odo: number;
  end_odo: number;
  start_read_on: string;
  end_read_on: string;
  days_covered: number;
  flag: '' | 'partial' | 'reset' | 'catchup';
}

interface MileageResponse {
  start: string;
  end: string;
  vehicles: MileageRow[];
  total_miles: number;
  no_data: string[];
}

/** Range start for the picker's convention: DateRangePresets computes
 *  ``days = end − start`` (its calendar label shows ``end − days``), so
 *  the query start must be exactly ``end − days`` — anything else makes
 *  the grid disagree with the label the user just picked. */
function startFor(end: string, days: number): string {
  const d = new Date(`${end}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

const FLAG_NOTE: Record<string, { label: string; tip: string }> = {
  catchup: {
    label: 'Catch-up days',
    tip: 'The odometer feed went silent for some days and then reported the backlog in one reading — the range total is real, but if the range starts inside such a gap it can include some earlier driving.',
  },
  partial: {
    label: 'Partial',
    tip: 'Odometer history starts inside this range — real miles are at least the number shown.',
  },
  reset: {
    label: 'Odometer reset',
    tip: 'The odometer dropped mid-range (device swap or reset) — miles are summed from daily readings instead.',
  },
};

// 730 days of stored history (the backend rejects older starts).
const RETENTION_DAYS = 730;

export default function Mileage() {
  const tz = useTimezone();
  const [days, setDays] = useState(30);
  // Row click → trips drill-in for THAT vehicle over the SAME range.
  const [drawer, setDrawer] = useState<{ name: string; miles: number } | null>(null);
  // null = range ends today (the presets path); a custom calendar pick
  // sets an explicit end and the backend honors it.
  const [endDay, setEndDay] = useState<string | null>(null);

  const end = endDay ?? todayInTimeZone(tz);
  const start = startFor(end, days);

  const { data, isLoading, isFetching, error, refetch } =
    useQuery<MileageResponse>({
      queryKey: ['vehicle-mileage', start, end],
      queryFn: () => apiJSON<MileageResponse>(
        `/vehicles/mileage?start=${start}&end=${end}`,
      ),
      staleTime: 5 * 60_000,
    });

  const rows = data?.vehicles ?? [];

  const columns = [
    { key: 'vehicle_name', label: 'Vehicle', sortable: true },
    { key: 'company', label: 'Company', sortable: true },
    {
      key: 'miles', label: 'Miles', sortable: true, aggregable: true,
      render: (v: unknown) => (
        <span className="font-medium">
          {Number(v ?? 0).toLocaleString()} mi
        </span>
      ),
    },
    {
      key: 'start_odo', label: 'Start odometer', sortable: true,
      render: (v: unknown) => (
        <span className="text-muted-foreground text-xs">
          {Math.round(Number(v ?? 0)).toLocaleString()}
        </span>
      ),
    },
    {
      key: 'end_odo', label: 'End odometer', sortable: true,
      render: (v: unknown) => (
        <span className="text-muted-foreground text-xs">
          {Math.round(Number(v ?? 0)).toLocaleString()}
        </span>
      ),
    },
    { key: 'days_covered', label: 'Days', sortable: true },
    {
      key: 'flag', label: 'Coverage', sortable: true,
      render: (v: unknown) => {
        const note = FLAG_NOTE[String(v ?? '')];
        if (!note) return null;
        return (
          <Tip label={note.tip}>
            <span className={`px-2 py-0.5 rounded-md text-xs font-medium ${toneClasses('warn')}`}>
              {note.label}
            </span>
          </Tip>
        );
      },
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-2 flex-wrap">
        <span className="text-sm text-muted-foreground">
          {data && rows.length > 0
            ? `${data.total_miles.toLocaleString()} mi across ${rows.length} vehicle${rows.length === 1 ? '' : 's'}`
            : ''}
        </span>
        <DateRangePresets
          value={days}
          onChange={(d) => { setDays(d); setEndDay(null); }}
          onApplyRange={(d, e) => { setDays(d); setEndDay(e); }}
          end={endDay}
          maxDays={RETENTION_DAYS}
          isFetching={isFetching}
        />
      </div>

      {error && rows.length === 0 ? (
        <ErrorState
          message={error instanceof Error ? error.message : 'Failed to load mileage'}
          onRetry={() => refetch()}
        />
      ) : isLoading ? (
        <TableSkeleton rows={8} cols={6} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Route}
          title="No mileage in this range"
          description="No vehicle has odometer readings inside the selected dates — widen the range, or check that telematics sync is running."
        />
      ) : (
        <DataGrid
          tableId="vehicle-mileage"
          columns={columns}
          data={rows as unknown as Record<string, unknown>[]}
          searchKey={['vehicle_name', 'company']}
          onRowClick={(row) => {
            const r = row as unknown as MileageRow;
            setDrawer({ name: r.vehicle_name, miles: r.miles });
          }}
        />
      )}

      {drawer && (
        <TripsDrawer
          vehicleName={drawer.name}
          rowMiles={drawer.miles}
          start={start}
          end={end}
          onClose={() => setDrawer(null)}
        />
      )}

      {(data?.no_data?.length ?? 0) > 0 && (
        <p className="mt-3 text-xs text-muted-foreground">
          No odometer data (not telematics-linked or silent in this range):{' '}
          {data!.no_data.join(', ')}
        </p>
      )}
    </div>
  );
}
