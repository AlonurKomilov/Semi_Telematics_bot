/**
 * Source — where this truck's record comes from, and where to open it
 * at the provider that supplies it.
 *
 * Two facts and a door.  The facts are creator + enrichers, derived
 * server-side from field provenance: one truck is routinely created by
 * one integration and enriched by another, and the single `source`
 * value could not say that.  The door is the provider deep link — the
 * same "Open in Samsara" the Telegram alerts carry, built by the same
 * helper, so the two can never disagree about where a truck lives.
 *
 * Links load separately from the page's own data: resolving a Samsara
 * org id can cost a call per company on a cold process, and this page
 * is the one that just stopped waiting on the provider.  A slow or
 * missing link costs a button, never a render.
 */
import { useQuery } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';

import { apiJSON } from '../../../api/client';
import { Card } from '@/components/ui/card';
import { SectionHeader } from '@/components/shell';
import { useVehicle } from './_shared/useVehicle';
import { orderedSources, sourceLabel, type ProviderLink } from '../sourceLabels';
import type { VehicleSectionProps } from './_shared/types';

export default function VehicleSource({ vehicleName, company }: VehicleSectionProps) {
  const { vehicle } = useVehicle(vehicleName, company);
  const registryId = vehicle?.registry_id ?? null;

  const { data } = useQuery<{ links: ProviderLink[] }>({
    queryKey: ['vehicle-links', registryId],
    queryFn: () => apiJSON(`/vehicles/registry/${registryId}/links`),
    enabled: registryId != null,
    // The org id behind these is stable for the life of an account —
    // re-asking on every focus would buy nothing and cost a round-trip.
    staleTime: 30 * 60 * 1000,
  });

  const all = orderedSources(vehicle?.sources, vehicle?.source);
  const links = data?.links ?? [];

  // A truck the registry has not caught yet has no provenance to state
  // and no id to ask about — render nothing rather than an empty card.
  if (registryId == null || all.length === 0) return null;

  const [creator, ...enrichers] = all;

  return (
    <Card className="mt-6">
      <SectionHeader>Source</SectionHeader>
      <dl className="mt-2 space-y-2">
        <div className="flex items-center justify-between gap-3">
          <dt className="text-sm text-muted-foreground">Created by</dt>
          <dd className="text-sm text-foreground">{sourceLabel(creator)}</dd>
        </div>
        {enrichers.length > 0 && (
          <div className="flex items-center justify-between gap-3">
            <dt className="text-sm text-muted-foreground">Enriched by</dt>
            <dd className="text-sm text-foreground">
              {enrichers.map(sourceLabel).join(' · ')}
            </dd>
          </div>
        )}
      </dl>

      {/* One door per provider that publishes a per-vehicle page.  A
          provider with no such URL contributes nothing here rather than
          a button that lands on a dashboard root — the truck the
          operator clicked must be the truck they get. */}
      {links.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
          {links.map((l) => (
            <a
              key={l.source}
              href={l.url} target="_blank" rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-sm text-primary hover:underline min-h-tap"
            >
              {l.label}
              <ExternalLink className="size-3.5" />
            </a>
          ))}
        </div>
      )}
    </Card>
  );
}
