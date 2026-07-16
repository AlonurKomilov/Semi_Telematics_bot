/**
 * FleetHero — operational status chips for the Fleet persona,
 * rendered INSIDE the topbar between the brand-left zone and the
 * tools cluster on the right.  Owner/Admin (DefaultShell) shows
 * nothing here; the persona shells substitute their own Hero
 * components which all share the same inline-chip geometry so the
 * topbar height stays constant across personas — no second row of
 * chrome pushing the content down.
 *
 * Strict persona binding: Fleet handles mechanical health (faults,
 * maintenance) — NOT fuel (that's Dispatch's job).  The chip set is:
 *
 *   Trucks · Moving · Idle · Stopped · No telemetry · Trailers ·
 *   Faults · Maintenance due
 *
 * Split by vehicle TYPE: motion states only mean something for
 * powered, tracked units — trailers (and no-telematics trucks) get
 * their own counts instead of inflating "Stopped".  Low-fuel moved
 * to DispatchHero with the rest of the live-ops triage signals.
 * Owner-as-Fleet sees this same hero (browser is on fleet.4truck.us,
 * SafetyShell/FleetShell renders based on subdomain → activeView →
 * shell).
 */
import { useShellStats } from './useShellStats';
import HeroChip from './HeroChip';

export default function FleetHero() {
  const { data, isLoading, isError } = useShellStats();
  if (isError) return <div className="flex-1 min-w-0" />;
  if (isLoading || !data) {
    return (
      <div className="flex-1 min-w-0 flex items-center px-2 gap-1.5">
        <span className="text-2xs text-muted-foreground/60">Loading fleet status…</span>
      </div>
    );
  }
  const { faults, maintenance_due } = data;
  // Role-neutral key with legacy-alias fallback (pre-rename API).
  const vehicles = data.vehicles ?? data.fleet ?? {};
  const trucks = vehicles.trucks;
  const trailers = vehicles.trailers;
  return (
    <div className="flex-1 min-w-0 flex items-center px-2 gap-1.5 overflow-x-auto scrollbar-thin">
      {trucks ? (
        <>
          <HeroChip label="Trucks" value={trucks.total} tone="info" />
          <HeroChip label="Moving" value={trucks.moving} tone="positive" />
          <HeroChip label="Idle" value={trucks.idle} tone="warning" />
          <HeroChip label="Stopped" value={trucks.stopped} tone="neutral" title="Engine off — tracked trucks only" />
          {trucks.no_signal > 0 && (
            // warning (not neutral): a powered unit that isn't reporting is
            // a monitoring-coverage gap — unlike trailers, where no device
            // is the normal case.
            <HeroChip label="No telemetry" value={trucks.no_signal} tone="warning" title="Registered trucks with no telematics reporting" />
          )}
          {trailers !== undefined && trailers.total > 0 && (
            <HeroChip
              label="Trailers" value={trailers.total} tone="info"
              title={trailers.no_signal < trailers.total
                ? `${trailers.total - trailers.no_signal} tracked · ${trailers.no_signal} without telematics`
                : 'Registered trailers — no telematics yet'}
            />
          )}
        </>
      ) : (
        // Pre-split API response (stale cache during deploy) — old chips.
        <>
          <HeroChip label="Total" value={vehicles.total} tone="info" />
          <HeroChip label="Moving" value={vehicles.moving} tone="positive" />
          <HeroChip label="Idle" value={vehicles.idle} tone="warning" />
          <HeroChip label="Stopped" value={vehicles.stopped} tone="neutral" />
        </>
      )}
      {faults !== undefined && faults > 0 && (
        <HeroChip label="Faults" value={faults} tone="critical" title="Active diagnostic fault codes" />
      )}
      {maintenance_due !== undefined && maintenance_due > 0 && (
        <HeroChip label="Maintenance due" value={maintenance_due} tone="warning" />
      )}
    </div>
  );
}
