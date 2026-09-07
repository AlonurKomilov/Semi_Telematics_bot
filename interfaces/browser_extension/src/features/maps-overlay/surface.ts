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
export function findMapCanvas<T extends { getBoundingClientRect(): DOMRectReadOnly }>(
  canvases: readonly T[],
): { el: T; surface: Surface } | null {
  let best: { el: T; surface: Surface } | null = null;
  for (const c of canvases) {
    const r = c.getBoundingClientRect();
    if (r.width < MIN_SIDE || r.height < MIN_SIDE) continue;
    if (!best || r.width * r.height > best.surface.width * best.surface.height) {
      best = { el: c, surface: { left: r.left, top: r.top, width: r.width, height: r.height } };
    }
  }
  return best;
}

export function findMapSurface(canvases: readonly { getBoundingClientRect(): DOMRectReadOnly }[]): Surface | null {
  return findMapCanvas(canvases)?.surface ?? null;
}

/**
 * Whether the map's box has to be measured again this tick.
 *
 * Measuring means asking the DOM for rectangles, and a rectangle is a
 * forced layout — the browser stops and re-computes the page to answer.
 * The overlay watches Google's URL eight times a second, and it used to
 * measure on every one of those ticks: eight forced layouts a second,
 * for the life of the tab, on a page that was usually not moving at all.
 *
 * Nothing needs measuring while all four of these hold: the URL is the
 * one we measured against, the canvas we measured is still in the page,
 * nothing has reported a resize, and the measurement is not older than
 * the recheck interval.  The last one is deliberate slack: Google's
 * markup is unversioned, so rather than trust that a resize always
 * reaches us, the box is re-read on a slow beat regardless.
 */
export function needsRemeasure(state: {
  url: string;
  measuredUrl: string;
  connected: boolean;
  geometryDirty: boolean;
  measuredAt: number;
  now: number;
  recheckMs: number;
}): boolean {
  if (state.geometryDirty || !state.connected) return true;
  if (state.url !== state.measuredUrl) return true;
  return state.now - state.measuredAt >= state.recheckMs;
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


/** How near a click must land to count as hitting a marker.  The dot is
 *  12px across; this is its radius plus a thumb's worth of forgiveness. */
export const HIT_RADIUS = 16;

/**
 * The marker under a point, or null.
 *
 * NEAREST within the radius, not first-found: trucks parked at one yard
 * overlap, and "whichever the loop reached first" would hand back a
 * different one each time the list re-ordered.  Nearest is stable and is
 * what the person aimed at.
 *
 * The markers themselves never take pointer events — taking the click
 * would take the DRAG too, and a drag that begins on a truck must still
 * move Google's map — so the hit test is ours to do.
 */
export function markerAt(
  drawn: ReadonlyMap<string, { x: number; y: number }>,
  x: number,
  y: number,
  radius = HIT_RADIUS,
): string | null {
  let best: string | null = null;
  let bestD = radius;
  for (const [id, p] of drawn) {
    const d = Math.hypot(p.x - x, p.y - y);
    if (d <= bestD) { bestD = d; best = id; }
  }
  return best;
}
