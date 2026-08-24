/**
 * Vehicle detail — Mileage section.
 *
 * "How far did THIS truck run in the period?"  Headline miles from the
 * odometer-delta engine plus a per-day bar strip (the daily ``miles``
 * buckets).  Same honesty rules as the account-wide Mileage tab: a
 * partial/reset range says so, and a vehicle with no usable odometer
 * history says that instead of showing 0.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { apiJSON } from '../../../api/client';
import { DateRangePresets } from '../../../components/shell';
import { Tip } from '../../../components/tooltip';
import { useTimezone } from '../../../hooks/useTimezone';
import { todayInTimeZone, formatDay } from '../../../utils/datetime';
import type { VehicleSectionProps } from './_shared/types';
import { flagCallout } from '../mileageFlags';
import { CalloutChip } from '../../../components/callouts';
import { Card } from '@/components/ui/card';

interface DayMiles { day: string; miles: number }

interface VehicleMileageResponse {
  start: string;
  end: string;
  no_data: boolean;
  miles?: number;
  start_odo?: number;
  end_odo?: number;
  days_covered?: number;
  flag?: '' | 'partial' | 'reset' | 'catchup' | 'estimated';
  days?: DayMiles[];
}

// Same convention as the picker's ``daysBetween``: start = end − days.
function startFor(end: string, days: number): string {
  const d = new Date(`${end}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}


export default function VehicleMileage({ vehicleName }: VehicleSectionProps) {
  const tz = useTimezone();
  const [days, setDays] = useState(30);
  const end = todayInTimeZone(tz);
  const start = startFor(end, days);

  const { data, isLoading, isFetching, error } =
    useQuery<VehicleMileageResponse>({
      queryKey: ['vehicle-mileage-detail', vehicleName, start, end],
      queryFn: () => apiJSON<VehicleMileageResponse>(
        `/vehicles/${encodeURIComponent(vehicleName)}/mileage?start=${start}&end=${end}`,
      ),
      staleTime: 5 * 60_000,
    });

  const bars = data?.days ?? [];
  const maxDay = Math.max(1, ...bars.map((b) => b.miles));

  return (
    <Card className="mt-6 lg:col-span-2">
      <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
        <h2 className="text-lg font-semibold">Mileage</h2>
        <DateRangePresets
          value={days}
          onChange={setDays}
          variant="segments"
          maxDays={730}
          isFetching={isFetching}
        />
      </div>

      {isLoading && (
        <p className="text-sm text-muted-foreground">Loading mileage…</p>
      )}
      {Boolean(error) && !isLoading && (
        <p className="text-sm text-destructive">Failed to load mileage.</p>
      )}
      {!isLoading && !error && data?.no_data && (
        <p className="text-sm text-muted-foreground">
          No odometer history for this vehicle in the selected range —
          not telematics-linked, or silent for the whole period.
        </p>
      )}

      {!isLoading && !error && data && !data.no_data && (
        <>
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className="text-2xl font-bold">
              {Number(data.miles ?? 0).toLocaleString()} mi
            </span>
            <span className="text-xs text-muted-foreground">
              odometer {Math.round(data.start_odo ?? 0).toLocaleString()} →{' '}
              {Math.round(data.end_odo ?? 0).toLocaleString()}
            </span>
            {flagCallout(data.flag) && (
              <CalloutChip callout={flagCallout(data.flag)!} />
            )}
          </div>

          {bars.length > 0 && (
            <div className="mt-4 flex items-end gap-px h-16" aria-label="Miles per day">
              {bars.map((b) => (
                <Tip key={b.day} label={`${formatDay(b.day, { timeZone: tz })} — ${b.miles.toLocaleString()} mi`}>
                  <div
                    className="flex-1 min-w-0.5 rounded-t-sm bg-primary/60 hover:bg-primary transition-colors"
                    style={{ height: `${Math.max(3, (b.miles / maxDay) * 100)}%` }}
                  />
                </Tip>
              ))}
            </div>
          )}
        </>
      )}
    </Card>
  );
}
