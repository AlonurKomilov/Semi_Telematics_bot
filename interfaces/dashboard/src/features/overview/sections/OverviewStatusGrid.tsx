/**
 * 4-up movement-status grid — Tier 3 of the Overview.
 *
 * Always renders the same four KPIs in the same 2/4-column grid:
 * total, moving, idle, stopped.  Tone changes (positive/warning/
 * critical) make the row scan-able at a glance.  Each tile is
 * click-through to /vehicles when the user has the underlying
 * permission — total has its own permission gate for the click;
 * moving/idle/stopped tiles are read-only summaries.
 */
import { Truck, Activity, CircleDot, CircleSlash } from 'lucide-react';
import { KpiCard } from '../../../components/shell';
import type { OverviewSectionProps } from './_shared/types';

export default function OverviewStatusGrid({ stats, navigate, has }: OverviewSectionProps) {
  const f = stats.fleet || {};
  const total = f.total ?? 0;
  // Motion tiles read the TRUCK bucket — trailers (and no-telematics
  // trucks) don't have a motion state, so percentages are computed
  // against tracked trucks, not the whole registry.
  const trucks = f.trucks ?? f;
  const truckTotal = trucks.total ?? 0;
  const moving = trucks.moving ?? 0;
  const idle = trucks.idle ?? 0;
  const stopped = trucks.stopped ?? 0;
  const trailerTotal = f.trailers?.total ?? 0;
  const pctOfTrucks = (n: number) =>
    truckTotal > 0 ? `${Math.round((n / truckTotal) * 100)}% of trucks` : undefined;

  // 4-up status row is the original visual order; suppressing the
  // section when zero is intentional — a brand-new tenant with no
  // ingestion yet shouldn't see "0 / 0 / 0 / 0" tiles.
  if (total === 0 && moving === 0 && idle === 0 && stopped === 0) {
    return null;
  }

  const vehiclesHref = has('can_vehicle_all') ? '/vehicles' : undefined;

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
      <KpiCard
        label="Total vehicles"
        value={total}
        icon={Truck}
        hint={
          trailerTotal > 0
            ? `${truckTotal} trucks · ${trailerTotal} trailers`
            : truckTotal > 0
              ? `${Math.round((moving / truckTotal) * 100)}% currently moving`
              : undefined
        }
        onClick={vehiclesHref ? () => navigate(vehiclesHref) : undefined}
      />
      <KpiCard
        label="Moving"
        value={moving}
        tone="positive"
        icon={Activity}
        hint={pctOfTrucks(moving)}
      />
      <KpiCard
        label="Idle"
        value={idle}
        tone="warning"
        icon={CircleDot}
        hint={pctOfTrucks(idle)}
      />
      <KpiCard
        label="Stopped"
        value={stopped}
        // Default (muted), not critical: majority-stopped is the NORMAL
        // overnight state — a red tile every morning trains alarm fatigue
        // and devalues real red signals (faults, unsafe parking).
        tone="default"
        icon={CircleSlash}
        hint={pctOfTrucks(stopped)}
      />
    </div>
  );
}
