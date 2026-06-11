/**
 * Vehicle detail header — back link + truck name + company.
 *
 * Always the first section in every persona's layout.  Renders the
 * name from the URL param immediately and the company from the
 * shared vehicle query once it lands — no loading-state needed since
 * the title is always known.
 */
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ChevronLeft } from 'lucide-react';
import { apiJSON } from '../../../api/client';
import type { Vehicle, VehiclesResponse } from '../../../types';
import type { VehicleSectionProps } from './_shared/types';

export default function VehicleHeader({ vehicleName, company }: VehicleSectionProps) {
  const { data } = useQuery<Vehicle | null>({
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

  // Spans the full grid width so the page heading sits above the
  // 2-col card grid on lg, even when this section is rendered as part
  // of the same grid container as the cards.
  return (
    <div className="lg:col-span-2">
      <Link
        to="/vehicles"
        className="inline-flex items-center gap-1 text-primary hover:underline text-sm mb-4"
      >
        <ChevronLeft size={14} />
        Back to vehicles
      </Link>
      <div className="flex items-baseline gap-3">
        <h1 className="text-2xl font-bold">{vehicleName}</h1>
        {data?.company && (
          <span className="text-sm text-muted-foreground">{data.company}</span>
        )}
      </div>
    </div>
  );
}
