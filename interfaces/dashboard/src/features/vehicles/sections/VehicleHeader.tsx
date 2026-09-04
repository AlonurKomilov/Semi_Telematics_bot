/**
 * Vehicle detail header — back link + truck name + company.
 *
 * Always the first section in every persona's layout.  Renders the
 * name from the URL param immediately and the company from the
 * shared vehicle query once it lands — no loading-state needed since
 * the title is always known.
 */
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft } from 'lucide-react';
import { apiJSON } from '../../../api/client';
import { Callout } from '../../../components/callouts';
import DeviceEventsCard from '../DeviceEventsCard';
import SourceMarks from '../SourceMarks';
import type { ProviderLink } from '../sourceLabels';
import { useViewPermissions } from '../../../hooks/useViewPermissions';
import { useVehicle, useVehicleCallouts } from './_shared/useVehicle';
import type { VehicleSectionProps } from './_shared/types';

export default function VehicleHeader({ vehicleName, company }: VehicleSectionProps) {
  const { vehicle: data } = useVehicle(vehicleName, company);
  // Conditions standing against this truck — rendered here, above
  // the cards, because they explain fields the reader is about to
  // find empty further down the page.
  const callouts = useVehicleCallouts(vehicleName, company);
  const { has } = useViewPermissions();

  // The same query key the Source card uses, so the two share ONE
  // request however the page is laid out.  A missing link costs a mark
  // its door, never the header its render.
  const registryId = data?.registry_id ?? null;
  const { data: linkData } = useQuery<{ links: ProviderLink[] }>({
    queryKey: ['vehicle-links', registryId],
    queryFn: () => apiJSON(`/vehicles/registry/${registryId}/links`),
    enabled: registryId != null,
    staleTime: 30 * 60 * 1000,
  });

  // Spans the full grid width so the page heading sits above the
  // 2-col card grid on lg, even when this section is rendered as part
  // of the same grid container as the cards.
  return (
    <div className="lg:col-span-2">
      <Link
        to="/vehicles"
        className="inline-flex items-center gap-1 text-primary hover:underline text-sm mb-3.5 py-0.5 min-h-tap"
      >
        <ChevronLeft className="size-3.5" />
        Back to vehicles
      </Link>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
        <h1 className="text-2xl font-bold">{vehicleName}</h1>
        {data?.company && (
          <span className="text-sm text-muted-foreground">{data.company}</span>
        )}
        {/* Who supplies this record, next to what it is called — the
            Source card at the foot of the page keeps the detail. */}
        <SourceMarks sources={data?.sources} source={data?.source}
                     links={linkData?.links ?? []} showLabel className="self-center" />
      </div>
      {/* Identity questions about THIS truck, in the same lane as its
          callouts.  They used to live only on the fleet-wide review
          list, which is how a VIN change can sit unanswered for weeks:
          nobody scrolls a list to find out why the truck they already
          have open looks wrong. */}
      <DeviceEventsCard
        canManage={has('can_manage_vehicles')}
        vehicles={[]}
        vehicleName={vehicleName}
        onResolved={() => { /* the page's own queries refetch on focus */ }}
      />
      {callouts.length > 0 && (
        <div className="mt-3 space-y-2">
          {callouts.map((c) => (
            <Callout key={c.key} callout={c} />
          ))}
        </div>
      )}
    </div>
  );
}
