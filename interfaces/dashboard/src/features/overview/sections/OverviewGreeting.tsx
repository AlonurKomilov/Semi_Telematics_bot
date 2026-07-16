/**
 * Personalised greeting card — "Welcome back, $name. $context."
 *
 * Universal section — every non-driver persona's layout includes it
 * at the top.  Greeting copy adapts via the ``context`` prop based on
 * fleet size; the heavy lifting is inside the shared ``Greeting``
 * component, this section just decides what context line to pass.
 */
import { Greeting } from '../../../components/shell';
import type { OverviewSectionProps } from './_shared/types';

export default function OverviewGreeting({ stats, greetingName }: OverviewSectionProps) {
  // Role-neutral key with legacy-alias fallback (pre-rename API).
  const vehicles = stats.vehicles ?? stats.fleet ?? {};
  const total = vehicles.total ?? 0;
  // Moving % is computed against TRUCKS — trailers can't move on their
  // own, and counting them made a healthy fleet read as 16% moving.
  const trucks = vehicles.trucks ?? vehicles;
  const truckTotal = trucks.total ?? 0;
  const movingPct = truckTotal > 0 ? Math.round(((trucks.moving ?? 0) / truckTotal) * 100) : 0;
  const trailerTotal = vehicles.trailers?.total ?? 0;

  const context =
    total > 0
      ? trailerTotal > 0
        ? `${truckTotal} trucks + ${trailerTotal} trailers · ${movingPct}% of trucks moving`
        : `${total} vehicles · ${movingPct}% currently moving`
      : 'Connect Samsara to start syncing vehicles.';

  return <Greeting name={greetingName} context={context} />;
}
