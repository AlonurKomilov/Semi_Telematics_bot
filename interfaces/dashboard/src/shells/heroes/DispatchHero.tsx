/**
 * DispatchHero — routing-status strip for the Dispatch persona.
 *
 * Emphasizes what a dispatcher cares about: vehicles on the road,
 * alerts to acknowledge, parking issues that need attention.  Until a
 * dedicated ``/api/routes/active/count`` endpoint exists, the
 * "Moving" count from the fleet stats is a workable proxy for
 * "active deliveries".
 *
 * Hides chips for fields the user doesn't have permission to see —
 * same gating as FleetHero (server-side permission filter on
 * /fleet/overview/stats).
 */
import { useShellStats } from './useShellStats';
import HeroChip from './HeroChip';

export default function DispatchHero() {
  const { data, isLoading, isError } = useShellStats();
  if (isError) return null;
  if (isLoading || !data) {
    return (
      <div className="h-9 bg-muted/20 border-b border-border/60 shrink-0 flex items-center px-4 gap-1.5">
        <span className="text-[11px] text-muted-foreground/60">Loading dispatch status…</span>
      </div>
    );
  }
  const { fleet, pending_alerts, unsafe_parking, unknown_parking } = data;
  return (
    <div className="h-9 bg-muted/20 border-b border-border/60 shrink-0 flex items-center px-4 gap-1.5 overflow-x-auto scrollbar-thin">
      <HeroChip label="On the road" value={fleet.moving} tone="positive" title="Vehicles currently moving" />
      <HeroChip label="Idle" value={fleet.idle} tone="warning" title="Engine on but stationary" />
      <HeroChip label="Stopped" value={fleet.stopped} tone="neutral" />
      <HeroChip label="Total" value={fleet.total} tone="info" />
      {pending_alerts !== undefined && pending_alerts > 0 && (
        <HeroChip label="Pending alerts" value={pending_alerts} tone="critical" title="Alerts awaiting acknowledgement" />
      )}
      {unsafe_parking !== undefined && unsafe_parking > 0 && (
        <HeroChip label="Parked unsafely" value={unsafe_parking} tone="critical" title="Vehicles parked outside safe zones" />
      )}
      {unknown_parking !== undefined && unknown_parking > 0 && (
        <HeroChip label="Parked (unknown zone)" value={unknown_parking} tone="warning" />
      )}
    </div>
  );
}
