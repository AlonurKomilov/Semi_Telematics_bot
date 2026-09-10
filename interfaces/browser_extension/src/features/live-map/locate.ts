/**
 * Where a truck is, for a surface that has no map.
 *
 * The Inventory panel draws no map and holds no coordinates — its rows
 * are counts and a unit number.  But somebody who has just picked a
 * vehicle there usually wants to see it, and hunting for a unit number
 * on Google's map by hand is the step the panel exists to remove.
 *
 * It lives in features/live-map/ rather than in features/inventory/
 * because a position is a LOCATION read, whatever asks for it: the route
 * it uses is gated on ``can_view_location``, and a caller that may not
 * hold that grant must not offer the control at all.  The panel checks
 * `features.includes('live-map')` before it ever calls this — see
 * /extension/me, which answers in feature ids for exactly this reason.
 */
import { apiJSON } from '../../api/client';

/** The list is thirty seconds fresh on the server, so asking faster
 *  spends quota on numbers that have not changed.  One answer serves
 *  every selection inside that window. */
const TTL_MS = 30_000;

let cache: { at: number; byRegistry: Map<number, [number, number]> } | null = null;

/** Drop what is remembered — a fresh sign-in may be a different person
 *  whose trucks are not these. */
export function forgetPositions(): void {
  cache = null;
}

interface Wire {
  features?: {
    geometry?: { coordinates?: unknown[] };
    properties?: { registry_id?: unknown };
  }[];
}

async function load(now: number): Promise<Map<number, [number, number]>> {
  if (cache && now - cache.at < TTL_MS) return cache.byRegistry;
  const out = await apiJSON<Wire>('/map/vehicles');
  const byRegistry = new Map<number, [number, number]>();
  for (const f of out.features ?? []) {
    const rid = Number(f.properties?.registry_id);
    const c = f.geometry?.coordinates;
    if (!Number.isFinite(rid) || !Array.isArray(c) || c.length < 2) continue;
    const lng = Number(c[0]);
    const lat = Number(c[1]);
    // A truck with no usable fix is not "at null island" — it is a truck
    // we cannot point at, and the caller is told so by its absence.
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;
    byRegistry.set(rid, [lat, lng]);
  }
  cache = { at: now, byRegistry };
  return byRegistry;
}

/** ``[lat, lng]`` for a registry id, or null when it has no usable fix,
 *  the read failed, or the caller may not have positions at all. */
export async function positionOf(
  registryId: number | null | undefined,
  now: number = Date.now(),
): Promise<[number, number] | null> {
  if (registryId == null) return null;
  try {
    return (await load(now)).get(Number(registryId)) ?? null;
  } catch {
    // A slow or refused position read costs the jump, never the panel.
    return null;
  }
}
