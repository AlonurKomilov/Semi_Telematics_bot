/**
 * Finding the map inside a page we do not own, and deciding when to
 * redraw over it.  Pure logic; the DOM work is next door.
 *
 * Google renders its map to a full-bleed canvas and floats the search
 * panel on top of it, so the canvas rectangle — not the window — is the
 * box whose CENTRE the URL's coordinate sits at.  Measuring the canvas
 * gets that right whether the panel is open, closed, or absent.
 */

/** The box the map is drawn in, in CSS pixels from the top-left of the
 *  viewport. */
export interface Surface {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Anything smaller than this is a thumbnail, a Street View strip or a
 *  chart — not the map. */
const MIN_SIDE = 320;

/**
 * The largest canvas that plausibly IS the map.
 *
 * Deliberately not a class-name lookup: Google's markup is unversioned
 * and its class names change without notice, so a selector is a thing
 * that breaks silently one Tuesday.  "The biggest canvas on the page,
 * if it is big enough to be a map" survives a rename, and returns null
 * — which means DRAW NOTHING — when the page is not what we expect.
 */
export function findMapSurface(canvases: readonly { getBoundingClientRect(): DOMRectReadOnly }[]): Surface | null {
  let best: Surface | null = null;
  for (const c of canvases) {
    const r = c.getBoundingClientRect();
    if (r.width < MIN_SIDE || r.height < MIN_SIDE) continue;
    if (!best || r.width * r.height > best.width * best.height) {
      best = { left: r.left, top: r.top, width: r.width, height: r.height };
    }
  }
  return best;
}

/** Two surfaces the same to the pixel — a resize observer fires for
 *  sub-pixel changes a person cannot see. */
export function sameSurface(a: Surface | null, b: Surface | null): boolean {
  if (!a || !b) return a === b;
  return Math.round(a.left) === Math.round(b.left)
    && Math.round(a.top) === Math.round(b.top)
    && Math.round(a.width) === Math.round(b.width)
    && Math.round(a.height) === Math.round(b.height);
}

/** The three status colours the panel and the dashboard already use.
 *  Repeated rather than imported so the content script bundle carries
 *  no React and no map code — it ships into every Google Maps tab the
 *  person opens, and its size is a cost they pay for our convenience. */
export const STATUS_COLOUR: Record<string, string> = {
  moving: '#22c55e',
  idle: '#f59e0b',
  stopped: '#ef4444',
};
export function colourFor(status: string): string {
  return STATUS_COLOUR[status] ?? STATUS_COLOUR.stopped;
}
