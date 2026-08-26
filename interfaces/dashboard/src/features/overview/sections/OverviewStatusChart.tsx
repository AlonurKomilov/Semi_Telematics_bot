/**
 * Vehicle-status distribution chart — Tier 3.5 of the Overview.
 *
 * Renders a small narrative summary above the donut chart that
 * highlights utilisation context ("Strong utilisation today — 72%
 * of trucks on the road" / "Most trucks idle or stopped — only 18%
 * moving").  The chart itself is heavy (recharts) so it stays
 * lazy-loaded with a skeleton fallback.
 *
 * Silently no-ops when there's nothing to chart (zero of each state).
 */
import { lazy, Suspense } from 'react';
import type { OverviewSectionProps } from './_shared/types';
import { Card } from '@/components/ui/card';

const FleetStatusChart = lazy(() => import('@/features/overview/FleetStatusChart'));

export default function OverviewStatusChart({ stats }: OverviewSectionProps) {
  // Role-neutral key with legacy-alias fallback (pre-rename API).
  const f = stats.vehicles ?? stats.fleet ?? {};
  // The donut charts motion states, which only tracked TRUCKS have —
  // the header count and percentages must use the same denominator or
  // the slices visibly don't sum to the label.
  const trucks = f.trucks ?? f;
  const total = trucks.total ?? 0;
  const moving = trucks.moving ?? 0;
  const idle = trucks.idle ?? 0;
  const stopped = trucks.stopped ?? 0;
  const movingPct = total > 0 ? Math.round((moving / total) * 100) : 0;

  if (moving === 0 && idle === 0 && stopped === 0) return null;

  return (
    <Card className="mb-6">
      <div className="flex items-center justify-between mb-3">
        <div>
          <p className="text-sm text-foreground font-medium">
            Vehicle status distribution
          </p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {movingPct >= 70
              ? `Strong utilization today — ${movingPct}% of trucks on the road.`
              : movingPct < 30
                ? `Most trucks idle or stopped — only ${movingPct}% moving.`
                : `${moving} of ${total} trucks moving (${movingPct}%).`}
          </p>
        </div>
        <p className="text-xs text-muted-foreground">{total} trucks</p>
      </div>
      <Suspense
        fallback={<div className="h-55 bg-muted/40 rounded animate-pulse" />}
      >
        <FleetStatusChart moving={moving} idle={idle} stopped={stopped} />
      </Suspense>
    </Card>
  );
}
