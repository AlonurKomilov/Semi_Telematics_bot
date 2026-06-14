/**
 * 7-day hourly activity timeline — miles / max speed / harsh events.
 *
 * The data is the warehouse hourly roll-up; query is cached 5 min so
 * switching between vehicle tabs doesn't refetch immediately.  Layout
 * lists this for Fleet (utilisation review) and Dispatch (planning
 * context) primarily; Safety also pulls it for incident timelines.
 */
import { useQuery } from '@tanstack/react-query';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { apiJSON } from '../../../api/client';
import type { VehicleSectionProps } from './_shared/types';

interface TimelinePoint {
  hour_utc: string;
  miles?: number | null;
  drive_min?: number | null;
  idle_min?: number | null;
  max_speed_mph?: number | null;
  harsh_event_count?: number | null;
}

interface TimelineResponse {
  name?: string;
  vehicle_id?: string;
  days?: number;
  points: TimelinePoint[];
  error?: string;
}

export default function VehicleTimeline({ vehicleName, company }: VehicleSectionProps) {
  const { data, isLoading, error } = useQuery<TimelineResponse>({
    queryKey: ['vehicle-timeline', vehicleName, company ?? ''],
    queryFn: () => {
      const companyQs = company ? `&company=${encodeURIComponent(company)}` : '';
      return apiJSON<TimelineResponse>(
        `/vehicles/${encodeURIComponent(vehicleName)}/timeline?days=7${companyQs}`,
      );
    },
    staleTime: 5 * 60_000,
  });

  const points = (data?.points ?? []).map((p) => ({
    ...p,
    label: p.hour_utc?.slice(5, 13).replace('T', ' '),
  }));

  return (
    <div className="mt-6 lg:col-span-2 bg-card border border-border rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">7-Day Activity</h2>
        <span className="text-xs text-muted-foreground">hourly roll-up</span>
      </div>
      {isLoading && <p className="text-sm text-muted-foreground">Loading timeline…</p>}
      {error && <p className="text-sm text-destructive">Failed to load timeline.</p>}
      {!isLoading && !error && points.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No telemetry data yet — the warehouse roll-up runs hourly.
        </p>
      )}
      {points.length > 0 && (
        <div style={{ width: '100%', height: 220 }}>
          <ResponsiveContainer>
            <LineChart data={points} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis dataKey="label" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip
                contentStyle={{
                  background: 'hsl(var(--card))',
                  border: '1px solid hsl(var(--border))',
                  borderRadius: 'var(--radius)',
                  fontSize: 12,
                }}
              />
              <Line type="monotone" dataKey="miles" stroke="var(--info)" strokeWidth={2} dot={false} name="Miles" />
              <Line type="monotone" dataKey="max_speed_mph" stroke="var(--ok)" strokeWidth={2} dot={false} name="Max mph" />
              <Line type="monotone" dataKey="harsh_event_count" stroke="var(--danger)" strokeWidth={2} dot={false} name="Harsh" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
