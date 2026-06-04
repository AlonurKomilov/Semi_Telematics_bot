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
  const total = stats.fleet?.total ?? 0;
  const moving = stats.fleet?.moving ?? 0;
  const movingPct = total > 0 ? Math.round((moving / total) * 100) : 0;

  const context =
    total > 0
      ? `${total} vehicles · ${movingPct}% currently moving`
      : 'Connect Samsara to start syncing vehicles.';

  return <Greeting name={greetingName} context={context} />;
}
