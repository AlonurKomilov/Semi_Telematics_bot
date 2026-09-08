/**
 * Where a vehicle sits on the pixels of somebody else's map.
 *
 * Google Maps gives an extension nothing to ask.  The page holds its
 * own map object behind a closure, so the only thing the outside world
 * can read is the URL — Google writes the camera into it as
 * ``@lat,lng,zoom`` — and the size of the canvas it drew on.  From
 * those two facts the Web Mercator projection that every slippy map
 * shares puts a coordinate back on a pixel.
 *
 * WHAT THIS BUYS AND WHAT IT COSTS.  The maths is exact: Google, our
 * Leaflet and OpenStreetMap all use EPSG:3857 with 256-pixel tiles, so
 * a marker placed this way lands where it belongs, to the pixel, for
 * any camera the URL describes.  What it cannot do is FOLLOW a drag —
 * Google rewrites the URL when the gesture ends, not while it runs.
 * Everything downstream is built around that one limitation rather
 * than pretending it away.
 *
 * Pure functions, no DOM: this file is the part that can be proven,
 * and it is proven against a camera taken off a real Google Maps tab.
 */

/** What the URL says the camera is looking at. */
export interface Camera {
  lat: number;
  lng: number;
  /** Google's zoom, the same scale Leaflet and OSM use. */
  zoom: number;
}

/** A point in CSS pixels from the top-left of the map viewport. */
export interface Point {
  x: number;
  y: number;
}

/** Web Mercator's tile size, and the latitude beyond which the
 *  projection runs to infinity.  Both are the standard values every
 *  slippy map shares — this is why the arithmetic transfers. */
const TILE = 256;
export const MAX_LAT = 85.05112878;

/** World pixels at a given zoom: the whole globe is 256·2^z across. */
export function worldSize(zoom: number): number {
  return TILE * Math.pow(2, zoom);
}

/** Longitude → x, in world pixels. */
export function lngToWorldX(lng: number, zoom: number): number {
  return ((lng + 180) / 360) * worldSize(zoom);
}

/** Latitude → y, in world pixels.  The clamp is not cosmetic: the
 *  Mercator y of a pole is infinite, and one NaN in a transform makes
 *  every marker on the layer disappear, not just the bad one. */
export function latToWorldY(lat: number, zoom: number): number {
  const clamped = Math.max(-MAX_LAT, Math.min(MAX_LAT, lat));
  const sin = Math.sin((clamped * Math.PI) / 180);
  const y = 0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI);
  return y * worldSize(zoom);
}

/**
 * A coordinate, in pixels from the top-left of a viewport of the given
 * size whose CENTRE is the camera.
 *
 * Google centres its map on the URL's coordinate, so the camera's own
 * world pixel is the middle of the box and everything else is measured
 * from there.
 */
export function project(
  point: { lat: number; lng: number },
  camera: Camera,
  viewport: { width: number; height: number },
): Point {
  const z = camera.zoom;
  return {
    x: lngToWorldX(point.lng, z) - lngToWorldX(camera.lng, z) + viewport.width / 2,
    y: latToWorldY(point.lat, z) - latToWorldY(camera.lat, z) + viewport.height / 2,
  };
}

/** Whether a projected point is worth drawing.  The margin keeps a
 *  marker whose ICON overhangs the edge from popping in and out as the
 *  map moves — half an icon showing is correct, a flicker is not. */
export function isVisible(p: Point, viewport: { width: number; height: number }, margin = 64): boolean {
  return p.x >= -margin && p.y >= -margin
      && p.x <= viewport.width + margin && p.y <= viewport.height + margin;
}

/**
 * The camera Google wrote into its own URL.
 *
 * The shape is ``/maps/@35.500878,-93.724251,12z`` and it survives
 * every 2D view: a place, a search, directions all carry the same
 * segment once the map has settled.  Returns null when it is absent —
 * which is the honest answer for the states this overlay must NOT draw
 * on: Street View (``/maps/@…,3a,…`` — a photo, not a map), the globe
 * view, and the moment before Google has written a camera at all.
 */
/** Metres of ground per pixel at the equator at zoom 0, Web Mercator
 *  on 256-pixel tiles.  Google's satellite URLs do not carry a zoom at
 *  all: they carry the height of the visible ground in METRES, and this
 *  constant is what turns that height back into a zoom. */
const METRES_PER_PIXEL_Z0 = 156543.03392;

/**
 * The camera Google wrote into its URL, or null when there is none.
 *
 * Two shapes.  The map writes `@lat,lng,12z` — a zoom.  Satellite and
 * the globe write `@lat,lng,1966123m` — the height of the visible
 * ground in metres, which is a zoom only once the viewport's height in
 * pixels is known: zoom = log2(metresPerPixel(lat) · heightPx / metres).
 * Without that height the `m` form is unreadable and the caller waits
 * for a measurement rather than guessing.  Street View puts a pose in
 * the third field (`3a`) and matches neither.
 *
 * The overlay spent its first week showing "loading…" for ever on any
 * satellite view, because only the `z` shape was read — and a person
 * comparing two installs concluded one of them was broken.
 */
export function cameraFromUrl(url: string, viewportHeightPx?: number): Camera | null {
  const m = /@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(\d+(?:\.\d+)?)(z|m)(?=[,/?#]|$)/.exec(url);
  if (!m) return null;
  const lat = Number(m[1]), lng = Number(m[2]), value = Number(m[3]);
  if (!Number.isFinite(lat) || !Number.isFinite(lng) || !Number.isFinite(value)) return null;
  if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
  let zoom: number;
  if (m[4] === 'z') {
    zoom = value;
  } else {
    if (!viewportHeightPx || viewportHeightPx <= 0 || value <= 0) return null;
    const metresPerPixelZ0 = METRES_PER_PIXEL_Z0 * Math.cos((lat * Math.PI) / 180);
    zoom = Math.log2((metresPerPixelZ0 * viewportHeightPx) / value);
    // Two decimals: Google rewrites the altitude by a metre or two on a
    // timer, and a camera that changes in the sixth decimal would have
    // every marker re-projected for a move nobody could see.
    zoom = Math.round(zoom * 100) / 100;
  }
  if (!Number.isFinite(zoom) || zoom < 0 || zoom > 24) return null;
  return { lat, lng, zoom };
}

/** Whether this URL names a view the overlay can draw at all — a map
 *  or satellite camera, as opposed to Street View or a page with no
 *  camera.  Lets the switch say "3D view" rather than "loading…" when
 *  the wait will never end. */
export function cameraDrawable(url: string): boolean {
  if (isStreetView(url)) return false;
  return /@-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?,\d+(?:\.\d+)?(z|m)(?=[,/?#]|$)/.test(url);
}

/** Street View replaces the map with a photosphere; an overlay of
 *  truck markers over a photograph is nonsense, so the caller hides
 *  itself rather than drawing. */
export function isStreetView(url: string): boolean {
  return /@-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?,[\d.]+a,/.test(url) || /\/maps\/@[^/]*,3a,/.test(url);
}

/** Two cameras close enough that redrawing would move nothing a person
 *  could see.  Google rewrites its URL on a timer as well as on a
 *  gesture, and re-projecting a hundred markers for a rounding change
 *  is work nobody asked for. */
export function sameCamera(a: Camera | null, b: Camera | null): boolean {
  if (!a || !b) return a === b;
  return a.zoom === b.zoom
    && Math.abs(a.lat - b.lat) < 1e-7
    && Math.abs(a.lng - b.lng) < 1e-7;
}


/**
 * Below this zoom a name beside every truck stops being information.
 *
 * The marker is one size at every zoom, but the density is not: a
 * national view puts a hundred name pills into a few hundred pixels and
 * the eastern half of the map becomes an unreadable block. Past this
 * point the dot alone says "a vehicle is here", which is all a map at
 * that scale can honestly say; the name comes back when there is room
 * for it. Chosen at 8 — roughly a metro area, where a handful of trucks
 * are in view rather than a fleet.
 */
export const LABEL_MIN_ZOOM = 8;

export function showsLabels(zoom: number): boolean {
  return zoom >= LABEL_MIN_ZOOM;
}
