/**
 * The vehicle-detail query, in ONE place.
 *
 * Three sections (Header, Info, Location) render from the same
 * `/vehicles/{name}` response and previously each declared their own
 * copy of the query — same key, same fetch, three chances to drift.
 * TanStack dedupes by key, so this hook changes no network behaviour;
 * it just stops the response shape being re-derived per section.
 *
 * It keeps the WHOLE response rather than `vehicles[0]`, because the
 * payload now also carries `callouts` — the standing statements about
 * this truck ("no engine data since May 12") that the page has to be
 * able to show beside the empty fields they explain.
 */
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../../../api/client';
import { byEntity, type CalloutData } from '../../../../components/callouts';
import type { Vehicle, VehiclesResponse } from '../../../../types';

interface VehicleResponse extends VehiclesResponse {
  callouts?: CalloutData[];
}

function useVehicleResponse(vehicleName: string, company?: string) {
  return useQuery<VehicleResponse>({
    queryKey: ['vehicle', vehicleName, company ?? ''],
    queryFn: async () => {
      const qs = company ? `?company=${encodeURIComponent(company)}` : '';
      return apiJSON<VehicleResponse>(
        `/vehicles/${encodeURIComponent(vehicleName)}${qs}`,
      );
    },
    staleTime: 30_000,
  });
}

/** The vehicle itself — `null` until it lands, or when not found. */
export function useVehicle(vehicleName: string, company?: string) {
  const q = useVehicleResponse(vehicleName, company);
  const vehicle: Vehicle | null = q.data?.vehicles?.[0] ?? null;
  return { ...q, vehicle };
}

/**
 * Callouts about THIS truck.  Matched by the telematics id the wire
 * addresses them with (`vehicle:<id>`); a callout carrying no entity
 * belongs to the surface itself and is included too.
 */
export function useVehicleCallouts(
  vehicleName: string, company?: string,
): CalloutData[] {
  const q = useVehicleResponse(vehicleName, company);
  const id = String(q.data?.vehicles?.[0]?.id ?? '');
  const grouped = byEntity(q.data?.callouts);
  return [...(grouped.get(`vehicle:${id}`) ?? []), ...(grouped.get('') ?? [])];
}

/**
 * The "this truck has left the fleet" statement, if there is one.
 *
 * Sections use it to stop promising data that is never coming: a
 * retired truck's timeline said "No telemetry data yet — the warehouse
 * roll-up runs hourly", which is a promise, and its mileage said
 * "Failed to load", which reads as a broken product.  Neither was
 * true; the truck left.
 *
 * Two keys because they are two facts — someone retired it, or its
 * gateway went silent and the sweep did.  Either way the page must
 * stop implying more is on the way.
 */
export function useArchivedCallout(
  vehicleName: string, company?: string,
): CalloutData | null {
  const callouts = useVehicleCallouts(vehicleName, company);
  return callouts.find(
    (c) => c.key === 'vehicle.archived'
        || c.key === 'vehicle.stopped_reporting',
  ) ?? null;
}
