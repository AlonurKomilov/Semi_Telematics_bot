/**
 * Location card — address, current speed, lat/lng with Google Maps
 * deep-link and clipboard copy.
 *
 * Shares the ``['vehicle', name]`` query key with VehicleInfo;
 * TanStack Query dedupes so this section adds zero network cost
 * when Info is also in the layout.
 */
import { CardSkeleton } from '../../../components/shell';
import type { Vehicle } from '../../../types';
import { LocationRows } from './_shared/LocationRows';
import { useVehicle } from './_shared/useVehicle';
import type { VehicleSectionProps } from './_shared/types';
import { Card } from '@/components/ui/card';

export default function VehicleLocation({ vehicleName, company }: VehicleSectionProps) {
  const { vehicle: v, isLoading } = useVehicle(vehicleName, company);

  if (isLoading || !v) return <CardSkeleton height="h-48" />;

  const loc = v.location || {};

  return (
    <Card className="space-y-3">
      <h2 className="text-lg font-semibold mb-3">Location</h2>
      <LocationRows
        address={loc.reverseGeo?.formattedLocation || v.formattedAddress || v.address}
        latitude={loc.latitude ?? v.latitude ?? null}
        longitude={loc.longitude ?? v.longitude ?? null}
        speedMph={v.speed_mph ?? null}
        ts={loc.time ?? null}
      />
    </Card>
  );
}
