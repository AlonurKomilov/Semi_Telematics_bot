/**
 * SafetyHero — compliance + alert strip for the Safety persona.
 *
 * Emphasizes incident-driven attention: open alerts, unsafe parking,
 * vehicles with active faults.  The fleet status fields are still
 * shown (total / moving) so the safety manager has fleet-wide
 * context without leaving their persona.
 *
 * Future expansion: a dedicated ``/api/safety/events?since=today``
 * endpoint could replace the alert-count chip with a "harsh events
 * today" chip more aligned with the safety workflow.  The strip layout
 * is structured so adding/swapping chips is a one-line change.
 */
import { useShellStats } from './useShellStats';
import HeroChip from './HeroChip';

export default function SafetyHero() {
  const { data, isLoading, isError } = useShellStats();
  if (isError) return null;
  if (isLoading || !data) {
    return (
      <div className="h-9 bg-muted/20 border-b border-border/60 shrink-0 flex items-center px-4 gap-1.5">
        <span className="text-[11px] text-muted-foreground/60">Loading safety overview…</span>
      </div>
    );
  }
  const { fleet, pending_alerts, unsafe_parking, faults } = data;
  return (
    <div className="h-9 bg-muted/20 border-b border-border/60 shrink-0 flex items-center px-4 gap-1.5 overflow-x-auto scrollbar-thin">
      {pending_alerts !== undefined && (
        <HeroChip
          label="Open alerts"
          value={pending_alerts}
          tone={pending_alerts > 0 ? 'critical' : 'positive'}
          title="Alerts awaiting acknowledgement"
        />
      )}
      {unsafe_parking !== undefined && (
        <HeroChip
          label="Unsafe parking"
          value={unsafe_parking}
          tone={unsafe_parking > 0 ? 'critical' : 'positive'}
        />
      )}
      {faults !== undefined && (
        <HeroChip
          label="Active faults"
          value={faults}
          tone={faults > 0 ? 'warning' : 'positive'}
        />
      )}
      <HeroChip label="Fleet total" value={fleet.total} tone="info" />
      <HeroChip label="Moving" value={fleet.moving} tone="neutral" />
    </div>
  );
}
