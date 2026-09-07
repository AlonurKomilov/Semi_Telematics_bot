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
