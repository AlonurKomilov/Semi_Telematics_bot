/**
 * Where to open ONE truck at the provider that supplies it.
 *
 * Asked per selection, not per list: resolving a provider's org id can
 * cost a call on a cold worker, and the map refreshes every thirty
 * seconds — paying that for thirty trucks nobody clicked would be the
 * expensive way to answer a question nobody asked.
 *
 * Answers are kept for the life of the panel: the URL for a truck does
 * not change while somebody is watching it move.
 */
import { apiJSON } from '../../api/client';

export interface ProviderLink { source: string; label: string; url: string }

const cache = new Map<number, ProviderLink[]>();

export async function linksFor(registryId: number | null | undefined): Promise<ProviderLink[]> {
  if (registryId == null) return [];
  const hit = cache.get(registryId);
  if (hit) return hit;
  try {
    const out = await apiJSON<{ links: ProviderLink[] }>(
      `/extension/vehicle-link?vehicle=${encodeURIComponent(String(registryId))}`);
    const links = out.links ?? [];
    cache.set(registryId, links);
    return links;
  } catch {
    // A provider that is slow or down costs a link, never the card.
    // Not cached: the next selection may well succeed.
    return [];
  }
}
