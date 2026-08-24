/**
 * 30-day usage summary — miles, drive/idle hours, utilization,
 * cost-per-mile, harsh-event count, plus a daily series chart.
 *
 * Backs different decisions for different personas reading the same
 * card: Fleet uses utilization % to spot under-used trucks; Accounting
 * uses cost-per-mile + work-order spend; Safety uses the harsh-event
 * count as a coaching signal; Mechanics use the daily series to spot
 * driving-pattern shifts that correlate with predictive-maintenance
 * trends.
 */
import { useQuery } from '@tanstack/react-query';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { apiJSON } from '../../../api/client';
import type { VehicleSectionProps } from './_shared/types';
import { Card } from '@/components/ui/card';

interface UsageSummary {
  vehicle_id: string;
  vehicle_name: string;
  days: number;
  days_with_data: number;
  total_miles: number;
  drive_hours: number;
  idle_hours: number;
  utilization_pct: number;
  max_speed_mph: number;
  // Null when no vehicle reported a fuel level in the window — an empty
  // tank reads 0 and must stay distinguishable from no reading at all.
  avg_fuel_pct: number | null;
  harsh_events: number;
  total_cost: number;
  work_order_count: number;
  cost_per_mile: number | null;
}

interface UsagePoint {
  day_utc: string;
  miles?: number | null;
  drive_min?: number | null;
  idle_min?: number | null;
  max_speed_mph?: number | null;
  avg_fuel_pct?: number | null;
  harsh_event_count?: number | null;
}

interface UsageResponse {
  name?: string;
  vehicle_id?: string;
  days?: number;
  summary: UsageSummary | null;
  series: UsagePoint[];
  error?: string;
}

function StatTile({
  label,
  value,
  suffix,
  hint,
}: {
  label: string;
  value: string;
  suffix?: string;
  hint?: string;
}) {
  return (
    <div className="bg-muted/40 border border-border rounded-md p-3">
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold tabular-nums">
        {value}
        {suffix && <span className="ml-1 text-sm font-normal text-muted-foreground">{suffix}</span>}
      </div>
      {hint && <div className="text-2xs text-muted-foreground mt-0.5">{hint}</div>}
    </div>
  );
}

export default function VehicleUsage({ vehicleName, company }: VehicleSectionProps) {
  const { data, isLoading, error } = useQuery<UsageResponse>({
    queryKey: ['vehicle-usage', vehicleName, company ?? '', 30],
    queryFn: () => {
      const companyQs = company ? `&company=${encodeURIComponent(company)}` : '';
      return apiJSON<UsageResponse>(
        `/vehicles/${encodeURIComponent(vehicleName)}/usage?days=30${companyQs}`,
      );
    },
    staleTime: 5 * 60_000,
  });

  const summary = data?.summary;
  const series = (data?.series ?? []).map((p) => ({
    ...p,
    label: p.day_utc?.slice(5),
    drive_hours: (p.drive_min ?? 0) / 60,
    idle_hours: (p.idle_min ?? 0) / 60,
  }));

  return (
    <Card className="mt-6 lg:col-span-2">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Usage Trends</h2>
        <span className="text-xs text-muted-foreground">last 30 days</span>
      </div>
      {isLoading && <p className="text-sm text-muted-foreground">Loading usage…</p>}
      {error && <p className="text-sm text-destructive">Failed to load usage.</p>}
      {!isLoading && !error && summary && summary.days_with_data === 0 && (
        <p className="text-sm text-muted-foreground">
          No usage data yet — the daily roll-up populates after the
          history table has a full day of snapshots.
        </p>
      )}
      {!isLoading && !error && summary && summary.days_with_data > 0 && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <StatTile
              label="Miles"
              value={summary.total_miles.toLocaleString()}
              hint={`${summary.days_with_data} of ${summary.days} days`}
            />
            <StatTile
              label="Drive"
              value={summary.drive_hours.toFixed(1)}
              suffix="h"
              hint={`${summary.idle_hours.toFixed(1)} h idle`}
            />
            <StatTile
              label="Utilization"
              value={summary.utilization_pct.toFixed(0)}
              suffix="%"
              hint="drive ÷ duty"
            />
            <StatTile
              label="Harsh events"
              value={String(summary.harsh_events)}
              hint={`max ${summary.max_speed_mph.toFixed(0)} mph`}
            />
            {summary.cost_per_mile != null && (
              <StatTile
                label="Cost / mile"
                value={`$${summary.cost_per_mile.toFixed(2)}`}
                hint={`$${summary.total_cost.toFixed(0)} across ${summary.work_order_count} WO`}
              />
            )}
            {summary.cost_per_mile == null && summary.total_cost > 0 && (
              <StatTile
                label="Work-order spend"
                value={`$${summary.total_cost.toFixed(0)}`}
                hint={`${summary.work_order_count} order${summary.work_order_count === 1 ? '' : 's'}`}
              />
            )}
            {summary.avg_fuel_pct != null && (
              <StatTile
                label="Avg fuel"
                value={summary.avg_fuel_pct.toFixed(0)}
                suffix="%"
              />
            )}
          </div>
          {series.length > 0 && (
            <div className="mt-5" style={{ width: '100%', height: 220 }}>
              <ResponsiveContainer>
                <BarChart data={series} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--card)',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius)',
                      fontSize: 12,
                    }}
                  />
                  <Bar dataKey="miles" fill="var(--info)" name="Miles" />
                  <Line type="monotone" dataKey="drive_hours" stroke="var(--ok)" strokeWidth={2} dot={false} name="Drive h" />
                  <Line type="monotone" dataKey="harsh_event_count" stroke="var(--danger)" strokeWidth={2} dot={false} name="Harsh" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
