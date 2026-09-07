/**
 * The one message the overlay sends, and the shape of the answer.
 *
 * A content script cannot use the extension's host permissions: its
 * fetches carry the PAGE's origin, so a request to api.4truck.us from
 * inside google.com/maps is a cross-site request the API would refuse.
 * The service worker has the permission and none of that problem, so
 * the overlay asks and the worker fetches.
 *
 * It also keeps the token where it already lives.  A content script
 * runs inside a page Google controls; handing it a bearer token so it
 * could call the API itself would put a live credential one XSS away
 * from a site we do not own.  The worker holds it, the overlay never
 * sees it, and the worst a compromised page can do is ask for the
 * positions the person is already looking at.
 */

export const OVERLAY_VEHICLES = '4truck:overlay-vehicles';
/** "A truck was clicked on Google's map — show it."  The worker opens
 *  the side panel; the choice itself travels through storage, so the
 *  panel finds it whether it was already open or opens because of this. */
export const OPEN_PANEL = '4truck:open-panel';

/** "Where is everything RIGHT NOW."  The list answer is thirty seconds
 *  old by design — it carries addresses, levels and provenance, and
 *  asking for all of that every five seconds would be wasteful.  This
 *  is the cheap half: positions only, the same feed the panel's map
 *  glides on, so a truck on Google's map moves like a truck on ours
 *  instead of teleporting twice a minute. */
export const OVERLAY_LIVE = '4truck:overlay-live';

/** One vehicle, trimmed to what a marker on somebody else's map needs.
 *  Deliberately not the full map payload: less to hand a page we do
 *  not control, and less to keep in sync. */
export interface OverlayVehicle {
  id: string;
  name: string;
  lat: number;
  lng: number;
  status: string;
  heading: number | null;
}

/** One live fix.  The wire shape of `/map/vehicles/live`, trimmed the
 *  same way the list is. */
export interface OverlayFix {
  id: string;
  lat: number;
  lng: number;
  speed_mph: number;
  heading: number | null;
}

export type LiveReply =
  | { ok: true; fixes: OverlayFix[] }
  | { ok: false };

interface LiveWire { positions?: Record<string, { lat?: unknown; lng?: unknown; speed_mph?: unknown; heading?: unknown }> }

/** The live payload, trimmed.  Shared by the worker and its tests. */
export function toOverlayFixes(wire: LiveWire): OverlayFix[] {
  const out: OverlayFix[] = [];
  for (const [id, pos] of Object.entries(wire?.positions ?? {})) {
    // `Number(null)` is 0, a perfectly finite coordinate off the coast
    // of Africa — the same phantom fix the server filters out of the
    // list.  A missing coordinate is rejected before it becomes one.
    if (pos?.lat == null || pos?.lng == null) continue;
    const lat = Number(pos.lat), lng = Number(pos.lng);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;
    const speed = Number(pos.speed_mph);
    const heading = pos.heading == null ? NaN : Number(pos.heading);
    out.push({
      id, lat, lng,
      speed_mph: Number.isFinite(speed) ? speed : 0,
      heading: Number.isFinite(heading) ? heading : null,
    });
  }
  return out;
}

export type OverlayReply =
  | { ok: true; vehicles: OverlayVehicle[] }
  /** Not signed in.  The overlay stays silent — an extension that nags
   *  on a page the person did not open for it is an extension they
   *  uninstall. */
  | { ok: false; reason: 'signed-out' }
  | { ok: false; reason: 'error'; detail: string };

interface MapFeature {
  geometry?: { coordinates?: [number, number] };
  properties?: Record<string, unknown>;
}

/** The map payload, trimmed. Shared by the worker and its tests. */
export function toOverlayVehicles(features: MapFeature[]): OverlayVehicle[] {
  const out: OverlayVehicle[] = [];
  for (const f of features ?? []) {
    const c = f.geometry?.coordinates;
    if (!c || c.length < 2) continue;
    const [lng, lat] = c;
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;
    const p = f.properties ?? {};
    const heading = typeof p.heading === 'number' && Number.isFinite(p.heading) ? p.heading : null;
    out.push({
      id: String(p.id ?? p.name ?? `${lat},${lng}`),
      name: String(p.name ?? ''),
      lat, lng,
      status: String(p.status ?? 'stopped'),
      heading,
    });
  }
  return out;
}
