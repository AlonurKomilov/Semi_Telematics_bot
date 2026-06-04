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
 *   Total · Moving · Idle · Stopped · Faults · Maintenance due
 *
 * Low-fuel moved to DispatchHero with the rest of the live-ops
 * triage signals.  Owner-as-Fleet sees this same hero (browser is
 * on fleet.4truck.us, SafetyShell/FleetShell renders based on
 * subdomain → activeView → shell).
 */
import { useShellStats } from './useShellStats';
import HeroChip from './HeroChip';

export default function FleetHero() {
  const { data, isLoading, isError } = useShellStats();
  if (isError) return <div className="flex-1 min-w-0" />;
  if (isLoading || !data) {
    return (
      <div className="flex-1 min-w-0 flex items-center px-2 gap-1.5">
        <span className="text-[11px] text-muted-foreground/60">Loading fleet status…</span>
      </div>
    );
  }
  const { fleet, faults, maintenance_due } = data;
  return (
    <div className="flex-1 min-w-0 flex items-center px-2 gap-1.5 overflow-x-auto scrollbar-thin">
      <HeroChip label="Total" value={fleet.total} tone="info" />
      <HeroChip label="Moving" value={fleet.moving} tone="positive" />
      <HeroChip label="Idle" value={fleet.idle} tone="warning" />
      <HeroChip label="Stopped" value={fleet.stopped} tone="neutral" />
      {faults !== undefined && faults > 0 && (
        <HeroChip label="Faults" value={faults} tone="critical" title="Active diagnostic fault codes" />
      )}
      {maintenance_due !== undefined && maintenance_due > 0 && (
        <HeroChip label="Maintenance due" value={maintenance_due} tone="warning" />
      )}
    </div>
  );
}
