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
  const moving = f.moving ?? 0;
  const idle = f.idle ?? 0;
  const stopped = f.stopped ?? 0;
  const movingPct = total > 0 ? Math.round((moving / total) * 100) : 0;

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
        hint={total > 0 ? `${movingPct}% currently moving` : undefined}
        onClick={vehiclesHref ? () => navigate(vehiclesHref) : undefined}
      />
      <KpiCard
        label="Moving"
        value={moving}
        tone="positive"
        icon={Activity}
        hint={total > 0 ? `${movingPct}% of total` : undefined}
      />
      <KpiCard
        label="Idle"
        value={idle}
        tone="warning"
        icon={CircleDot}
        hint={total > 0 ? `${Math.round((idle / total) * 100)}% of total` : undefined}
      />
      <KpiCard
        label="Stopped"
        value={stopped}
        tone="critical"
        icon={CircleSlash}
        hint={total > 0 ? `${Math.round((stopped / total) * 100)}% of total` : undefined}
      />
    </div>
  );
}
