/**
 * Location card — address, current speed, lat/lng with Google Maps
 * deep-link and clipboard copy.
 *
 * Shares the ``['vehicle', name]`` query key with VehicleInfo;
 * TanStack Query dedupes so this section adds zero network cost
 * when Info is also in the layout.
 */
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../../api/client';
import { CardSkeleton } from '../../../components/shell';
import type { Vehicle, VehiclesResponse } from '../../../types';
import { LocationRows } from './_shared/LocationRows';
import type { VehicleSectionProps } from './_shared/types';

export default function VehicleLocation({ vehicleName, company }: VehicleSectionProps) {
  const { data: v, isLoading } = useQuery<Vehicle | null>({
    queryKey: ['vehicle', vehicleName, company ?? ''],
    queryFn: async () => {
      const qs = company ? `?company=${encodeURIComponent(company)}` : '';
      const res = await apiJSON<VehiclesResponse>(
        `/vehicles/${encodeURIComponent(vehicleName)}${qs}`,
      );
      return res.vehicles?.[0] ?? null;
    },
    staleTime: 30_000,
  });

  if (isLoading || !v) return <CardSkeleton height="h-48" />;

  const loc = v.location || {};

  return (
    <div className="bg-card border border-border rounded-xl p-5 space-y-3">
      <h2 className="text-lg font-semibold mb-3">Location</h2>
      <LocationRows
        address={loc.reverseGeo?.formattedLocation || v.formattedAddress || v.address}
        latitude={loc.latitude ?? v.latitude ?? null}
        longitude={loc.longitude ?? v.longitude ?? null}
        speedMph={v.speed_mph ?? null}
        ts={loc.time ?? null}
      />
    </div>
  );
}
