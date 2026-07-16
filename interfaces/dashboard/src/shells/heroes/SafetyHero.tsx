/**
 * SafetyHero — alert strip for the Safety persona.
 *
 * Strict persona binding: Safety's job is events + camera + coaching
 * — NOT faults (Fleet) or unsafe parking (Dispatch).  The chip set
 * keeps only what a safety manager actually triages:
 *
 *   Open alerts (safety-scoped via X-View-As) · Trucks · Moving
 *
 * The fleet status chips give the safety manager fleet-wide context
 * without crossing into Fleet's workspace.  Faults / unsafe-parking
 * chips were removed when strict binding shipped — those belong on
 * FleetHero and DispatchHero respectively.  A future
 * ``/api/safety/events?since=today`` endpoint could add a
 * "harsh events today" chip more aligned with safety's workflow.
 */
import { useShellStats } from './useShellStats';
import HeroChip from './HeroChip';

export default function SafetyHero() {
  const { data, isLoading, isError } = useShellStats();
  if (isError) return <div className="flex-1 min-w-0" />;
  if (isLoading || !data) {
    return (
      <div className="flex-1 min-w-0 flex items-center px-2 gap-1.5">
        <span className="text-2xs text-muted-foreground/60">Loading safety overview…</span>
      </div>
    );
  }
  const { pending_alerts } = data;
  // Role-neutral key with legacy-alias fallback (pre-rename API).
  const vehicles = data.vehicles ?? data.fleet ?? {};
  const trucks = vehicles.trucks ?? vehicles;
  return (
    <div className="flex-1 min-w-0 flex items-center px-2 gap-1.5 overflow-x-auto scrollbar-thin">
      {pending_alerts !== undefined && (
        <HeroChip
          label="Open alerts"
          value={pending_alerts}
          tone={pending_alerts > 0 ? 'critical' : 'positive'}
          title="Alerts awaiting acknowledgement"
        />
      )}
      <HeroChip
        label="Trucks" value={trucks.total} tone="info"
        title={vehicles.trailers && vehicles.trailers.total > 0
          ? `${vehicles.total} vehicles incl. ${vehicles.trailers.total} trailers`
          : undefined}
      />
      <HeroChip label="Moving" value={trucks.moving} tone="neutral" />
    </div>
  );
}
