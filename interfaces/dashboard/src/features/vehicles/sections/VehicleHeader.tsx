/**
 * Vehicle detail header — back link + truck name + company.
 *
 * Always the first section in every persona's layout.  Renders the
 * name from the URL param immediately and the company from the
 * shared vehicle query once it lands — no loading-state needed since
 * the title is always known.
 */
import { Link } from 'react-router-dom';
import { ChevronLeft } from 'lucide-react';
import { Callout } from '../../../components/callouts';
import DeviceEventsCard from '../DeviceEventsCard';
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
      <div className="flex items-baseline gap-3">
        <h1 className="text-2xl font-bold">{vehicleName}</h1>
        {data?.company && (
          <span className="text-sm text-muted-foreground">{data.company}</span>
        )}
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
            // ``entity``: a dismissal is recorded against the TRUCK, so
            // it lands on that vehicle's activity trail — where an
            // owner asking "was anyone told about this?" would actually
            // look.  Without it the entry files under a callout id
            // nobody browses.
            <Callout
              key={c.key}
              callout={c}
              entity={{ type: 'vehicle', id: vehicleName }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
