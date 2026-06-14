/**
 * 30-day fleet utilization summary card.
 *
 * Lives on the Vehicles page above the table.  Shows fleet-wide
 * drive vs idle totals plus the top utilized / underutilized trucks
 * so operators can find candidates for assignment or retirement at a
 * glance.  Hidden when the warehouse history is still cold (returns
 * empty `vehicles[]`) — the page falls through to just the table.
 */
import { useQuery } from '@tanstack/react-query';
import { Gauge } from 'lucide-react';
import { apiJSON } from '../../api/client';

interface UtilizationRow {
  vehicle_id: string;
  vehicle_name: string;
  company_code: string;
  total_miles: number;
  drive_hours: number;
  idle_hours: number;
  utilization_pct: number;
  days_with_data: number;
}

interface UtilizationResponse {
  days: number;
  vehicles: UtilizationRow[];
}

function pct(n: number) {
  return `${Math.round(n)}%`;
}

export default function UtilizationSummary() {
  const { data, isLoading } = useQuery<UtilizationResponse>({
    queryKey: ['fleet-utilization', 30],
    queryFn: () => apiJSON<UtilizationResponse>('/vehicles/utilization-summary?days=30'),
    staleTime: 10 * 60_000,
  });

  if (isLoading || !data || data.vehicles.length === 0) return null;

  const rows = data.vehicles;
  const fleetDriveH = rows.reduce((a, r) => a + (r.drive_hours || 0), 0);
  const fleetIdleH = rows.reduce((a, r) => a + (r.idle_hours || 0), 0);
  const fleetMiles = rows.reduce((a, r) => a + (r.total_miles || 0), 0);
  const dutyH = fleetDriveH + fleetIdleH;
  const fleetUtilPct = dutyH > 0 ? (fleetDriveH / dutyH) * 100 : 0;

  const top = rows.slice(0, 3);
  // Sort ascending for the underutilized list — already DESC from the
  // API, so reverse + take from the tail.
  const bottom = [...rows].reverse().slice(0, 3);

  return (
    <div className="mb-4 bg-card border border-border rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <Gauge size={16} className="text-muted-foreground" />
        <h2 className="text-base font-semibold">Utilization — last 30 days</h2>
        <span className="ml-auto text-xs text-muted-foreground">
          {rows.length} {rows.length === 1 ? 'vehicle' : 'vehicles'} reporting
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <div className="bg-muted/40 border border-border rounded-md p-3">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Fleet utilization</div>
          <div className="mt-1 text-lg font-semibold tabular-nums">{pct(fleetUtilPct)}</div>
          <div className="text-2xs text-muted-foreground mt-0.5">drive ÷ duty</div>
        </div>
        <div className="bg-muted/40 border border-border rounded-md p-3">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Total miles</div>
          <div className="mt-1 text-lg font-semibold tabular-nums">{Math.round(fleetMiles).toLocaleString()}</div>
        </div>
        <div className="bg-muted/40 border border-border rounded-md p-3">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Drive hours</div>
          <div className="mt-1 text-lg font-semibold tabular-nums">{Math.round(fleetDriveH).toLocaleString()}</div>
        </div>
        <div className="bg-muted/40 border border-border rounded-md p-3">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Idle hours</div>
          <div className="mt-1 text-lg font-semibold tabular-nums">{Math.round(fleetIdleH).toLocaleString()}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1.5">
            Most utilized
          </div>
          <ul className="space-y-1">
            {top.map((r) => (
              <li key={r.vehicle_id} className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2">
                  <span className="font-mono font-semibold">{r.vehicle_name}</span>
                  {r.company_code && (
                    <span className="px-1.5 py-0.5 bg-muted rounded text-xs text-muted-foreground">{r.company_code}</span>
                  )}
                </span>
                <span className="tabular-nums text-ok">{pct(r.utilization_pct)}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1.5">
            Least utilized
          </div>
          <ul className="space-y-1">
            {bottom.map((r) => (
              <li key={r.vehicle_id} className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2">
                  <span className="font-mono font-semibold">{r.vehicle_name}</span>
                  {r.company_code && (
                    <span className="px-1.5 py-0.5 bg-muted rounded text-xs text-muted-foreground">{r.company_code}</span>
                  )}
                </span>
                <span className="tabular-nums text-warn">{pct(r.utilization_pct)}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
