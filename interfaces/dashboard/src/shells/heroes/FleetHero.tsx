/**
 * FleetHero — operational status strip for the Fleet persona.
 *
 * Shows what a fleet manager needs to know at a glance:
 *   Total · Moving · Idle · Stopped · Low fuel · Faults · Maintenance due
 *
 * Hides chips for fields the user doesn't have permission to see
 * (the API already gates ``can_faults`` / ``can_fuel`` etc., so
 * undefined → skip rendering).  The strip is persistent — it stays
 * visible as the user navigates between Fleet pages.
 */
import { useShellStats } from './useShellStats';
import HeroChip from './HeroChip';

export default function FleetHero() {
  const { data, isLoading, isError } = useShellStats();
  if (isError) return null;  // silent — main pages will surface the error
  if (isLoading || !data) {
    return (
      <div className="h-9 bg-muted/20 border-b border-border/60 shrink-0 flex items-center px-4 gap-1.5">
        <span className="text-[11px] text-muted-foreground/60">Loading fleet status…</span>
      </div>
    );
  }
  const { fleet, low_fuel, faults, maintenance_due } = data;
  return (
    <div className="h-9 bg-muted/20 border-b border-border/60 shrink-0 flex items-center px-4 gap-1.5 overflow-x-auto scrollbar-thin">
      <HeroChip label="Total" value={fleet.total} tone="info" />
      <HeroChip label="Moving" value={fleet.moving} tone="positive" />
      <HeroChip label="Idle" value={fleet.idle} tone="warning" />
      <HeroChip label="Stopped" value={fleet.stopped} tone="neutral" />
      {low_fuel !== undefined && low_fuel > 0 && (
        <HeroChip label="Low fuel" value={low_fuel} tone="warning" title="Vehicles below 20% fuel" />
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
