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
 * /overview/stats).
 */
import { useShellStats } from './useShellStats';
import HeroChip, { oldestCriticalAge } from './HeroChip';

export default function DispatchHero() {
  const { data, isLoading, isError } = useShellStats();
  if (isError) return <div className="flex-1 min-w-0" />;
  if (isLoading || !data) {
    return (
      <div className="flex-1 min-w-0 flex items-center px-2 gap-1.5">
        <span className="text-2xs text-muted-foreground/60">Loading dispatch status…</span>
      </div>
    );
  }
  const {
    pending_alerts, unsafe_parking, unknown_parking, low_fuel,
    oldest_critical_first_seen,
  } = data;
  // Role-neutral key with legacy-alias fallback (pre-rename API).
  const vehicles = data.vehicles ?? data.fleet ?? {};
  // Dispatch routes powered units — motion chips read the TRUCK bucket
  // (falls back to the flat counts on a pre-split API response).
  const trucks = vehicles.trucks ?? vehicles;
  return (
    <div className="flex-1 min-w-0 flex items-center px-2 gap-1.5 overflow-x-auto overflow-y-hidden scrollbar-thin">
      <HeroChip label="On the road" value={trucks.moving} tone="positive" title="Trucks currently moving" />
      <HeroChip label="Idle" value={trucks.idle} tone="warning" title="Engine on but stationary" />
      <HeroChip label="Stopped" value={trucks.stopped} tone="neutral" />
      <HeroChip label="Trucks" value={trucks.total} tone="info" />
      {pending_alerts !== undefined && pending_alerts > 0 && (
        <HeroChip label="Open alerts" value={pending_alerts} tone="critical" title="Alerts awaiting acknowledgement, limited to the types this view handles. The Alerts board lists every type, so its total is larger." />
      )}
      {oldest_critical_first_seen && (
        <HeroChip
          label="Oldest critical"
          value={oldestCriticalAge(oldest_critical_first_seen)}
          tone="critical"
          title="How long the longest-waiting open critical has been unacknowledged. A count tells you how much is open; this tells you whether anything is being left."
        />
      )}
      {unsafe_parking !== undefined && unsafe_parking > 0 && (
        <HeroChip label="Parked unsafely" value={unsafe_parking} tone="critical" title="Vehicles parked outside safe zones" />
      )}
      {unknown_parking !== undefined && unknown_parking > 0 && (
        <HeroChip label="Parked (unknown zone)" value={unknown_parking} tone="warning" />
      )}
      {low_fuel !== undefined && low_fuel > 0 && (
        <HeroChip label="Low fuel" value={low_fuel} tone="warning" title="Vehicles below 20% fuel" />
      )}
    </div>
  );
}
