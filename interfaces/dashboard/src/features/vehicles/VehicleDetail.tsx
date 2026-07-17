/**
 * Vehicle Detail page — Pattern B composition.
 *
 * The page itself is now ~20 lines: it pulls the ``:name`` URL param,
 * hands it to PageLayoutHost as the section prop, and lets the
 * persona's layout (from ``layouts.ts``) decide which sections from
 * the registry render in what order.
 *
 * The grid container preserves the original visual: a 2-column card
 * grid on ``lg`` and above (Info / Location / Health / Faults filling
 * cells), with the Header at top and Inspections + Timeline spanning
 * full width below — each of those three sections opts in via
 * ``lg:col-span-2`` on its own root.
 */
import { useMemo } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { PageLayoutHost } from '../_lib/PageLayoutHost';
import { CardSkeleton } from '../../components/shell';
import { usePublishContext } from '../ai/PageContext';
import { VEHICLE_SECTIONS } from './registry';
import { VEHICLE_LAYOUTS } from './layouts';

export default function VehicleDetail() {
  const { name } = useParams<{ name: string }>();
  // The ``?company=`` URL search param disambiguates cross-company
  // vehicle-name collisions (one account can have a "103" in G1 AND
  // a "103" in OSY).  Without it, the /vehicles/{name} endpoint
  // returns both matches and every section on this page would
  // silently render data from whichever truck happened to land in
  // ``vehicles[0]`` — often the wrong one.
  const [search] = useSearchParams();
  const company = search.get('company') || undefined;

  // The assistant's "this truck": publish the opened vehicle as the
  // page focus so questions like "any faults here?" resolve to it.
  usePublishContext(useMemo(() => name ? {
    feature: 'vehicles',
    label: 'Vehicle',
    focus: {
      kind: 'vehicle',
      id: name,
      label: company ? `${name} (${company})` : name,
    },
  } : null, [name, company]));

  if (!name) return null;
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <PageLayoutHost
        registry={VEHICLE_SECTIONS}
        layouts={VEHICLE_LAYOUTS}
        sectionProps={{ vehicleName: name, company }}
        sectionFallback={<CardSkeleton height="h-32" />}
      />
    </div>
  );
}
