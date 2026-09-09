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

/** The same question, asked by the PANEL rather than by a page.
 *
 *  The panel could fetch this itself — it is our own context and it
 *  holds the token — and it did.  But then a person with the panel open
 *  beside a Google Maps tab was asking for one fleet's positions twice
 *  every five seconds, on two clocks, and seeing two slightly different
 *  instants.  Going through the worker means one answer, shared, and
 *  both surfaces showing the same moment.
 *
 *  The reply is the payload as it arrived, not the trimmed one: the
 *  panel needs the reading's age, which a marker on somebody else's map
 *  does not.  That is safe here and would not be there — this answer
 *  never leaves the extension. */
export const PANEL_LIVE = '4truck:panel-live';

/** A truck chosen on google.com/maps, waiting for the panel to read it.
 *  Storage rather than a message, so it works whether the panel was
 *  already open or is opening because of that very click.
 *
 *  It carries an identity for each reader.  Live Map matches the map's
 *  own id; Inventory has no map and matches the unit number within its
 *  company — the fleet answer it opens on is keyed that way, and it
 *  cannot ask /map/vehicles for a translation because that route needs
 *  the location grant this reader may not have. */
export const PENDING_SELECT_KEY = 'pendingSelectVehicle';

export interface PendingSelect { id: string; name: string; company: string }

/** Read whatever is in the key.  Tolerant of the BARE STRING the key
 *  held before Inventory existed: an update lands while a click may
 *  already be sitting there, and dropping it would eat that click. */
export function readPendingSelect(v: unknown): PendingSelect | null {
  if (typeof v === 'string') return v ? { id: v, name: '', company: '' } : null;
  if (!v || typeof v !== 'object') return null;
  const o = v as Record<string, unknown>;
  const id = typeof o.id === 'string' ? o.id : '';
  const name = typeof o.name === 'string' ? o.name : '';
  if (!id && !name) return null;
  return { id, name, company: typeof o.company === 'string' ? o.company : '' };
}

/** Which feature the panel is showing, so the overlay's card can say
 *  where its button goes.  A label promising fuel levels while the
 *  panel is on Inventory reads as a bug, and it is one. */
export const ACTIVE_FEATURE_KEY = 'panelFeature';

/** One vehicle, trimmed to what the overlay draws AND what its card
 *  answers.  Still not the full map payload — the page is Google's, not
 *  ours — and every field below earns its place:
 *
 *    id, lat, lng, heading   the marker itself
 *    name + company          WHICH truck: unit numbers repeat across
 *                            companies, so "229" alone names nothing
 *                            and a card can name the wrong truck
 *    status + speed          what it is DOING; the map draws a heading,
 *                            never a speed
 *    updated_at              how old the reading is.  A marker on a live
 *                            map with no age is the lie the panel's
 *                            fold was: a three-week-old position drawn
 *                            beside moving traffic reads as moving
 *
 *  Each of the three additions answers "is this card telling the truth
 *  about which truck and when", which a card cannot do without them.
 *
 *  What stays OUT, and stays out on purpose: fuel and DEF levels,
 *  addresses, fault counts, provenance, registry ids.  They were
 *  tempting — a dispatcher planning a route wants the tank — but the
 *  card carries a button to the panel, which is where they live and
 *  where the page cannot read them.  "It is already exposed, one more
 *  field is marginal" is how a boundary stops being one. */
export interface OverlayVehicle {
  id: string;
  name: string;
  lat: number;
  lng: number;
  status: string;
  heading: number | null;
  company: string;
  speed_mph: number;
  updated_at: string;
  /** What is aboard — present ONLY while the panel is on Inventory, so
   *  the Live Map's pages never carry it.  Counts, never contents: a
   *  label or a serial number answers the dashboard's questions, and
   *  this is a page we do not own. */
  inventory_total?: number;
  inventory_attention?: number;
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

/** The panel's reply: the wire as it came.  ``ok: false`` means the
 *  worker could not answer, and the panel asks the API itself — the
 *  shared answer is an economy, never a dependency. */
export type PanelLiveReply<T> =
  | { ok: true; wire: T }
  | { ok: false };

/** Take the shared answer when there is one, ask for your own when
 *  there is not.
 *
 *  The rule this pins: sharing is an ECONOMY, never a dependency.  A
 *  worker that is asleep, updating, or simply unable to answer must
 *  cost a caller one extra request — never its data. */
export async function sharedOrOwn<T>(
  askShared: () => Promise<PanelLiveReply<T>>,
  askDirectly: () => Promise<T>,
): Promise<T> {
  const shared = await askShared();
  return shared.ok ? shared.wire : askDirectly();
}

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
/** ``registry id -> counts``, supplied by the worker ONLY while the panel
 *  is showing Inventory.  On Live Map the fields are absent, so a page
 *  belonging to somebody else never carries what is aboard our trucks
 *  when nobody asked what is aboard our trucks. */
export type InventoryCounts = Map<number, { total: number; attention: number }>;

export function toOverlayVehicles(
  features: MapFeature[], inventory?: InventoryCounts,
): OverlayVehicle[] {
  const out: OverlayVehicle[] = [];
  for (const f of features ?? []) {
    const c = f.geometry?.coordinates;
    if (!c || c.length < 2) continue;
    const [lng, lat] = c;
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;
    const p = f.properties ?? {};
    const heading = typeof p.heading === 'number' && Number.isFinite(p.heading) ? p.heading : null;
    const speed = Number(p.speed_mph);
    const inv = inventory?.get(Number(p.registry_id));
    out.push({
      id: String(p.id ?? p.name ?? `${lat},${lng}`),
      name: String(p.name ?? ''),
      lat, lng,
      status: String(p.status ?? 'stopped'),
      heading,
      company: String(p.company ?? ''),
      speed_mph: Number.isFinite(speed) ? speed : 0,
      updated_at: String(p.updated_at ?? ''),
      // The join happens HERE so registry_id stays behind: it is the
      // key, never the payload.  Absent rather than zero when the truck
      // carries nothing — a card should say "3 items" or say nothing,
      // not announce an emptiness nobody asked about.
      ...(inv ? { inventory_total: inv.total, inventory_attention: inv.attention } : {}),
    });
  }
  return out;
}
