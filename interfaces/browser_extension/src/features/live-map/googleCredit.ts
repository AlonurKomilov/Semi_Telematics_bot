/**
 * Google's name and its per-view copyright line, on the map that is
 * drawing Google's tiles.
 *
 * This is not decoration.  The Map Tiles API terms require the
 * attribution to be displayed, and the copyright string is per
 * VIEWPORT — it changes as the map moves, which is why there is a
 * service to ask rather than a constant to print.  The panel drew
 * Google tiles for one version with no attribution at all: the tile
 * layer's own `attribution` is deliberately empty (a fixed string
 * would be wrong half the time) and the caller that was supposed to
 * render it never did.
 *
 * The dashboard puts this in the corner OPPOSITE Leaflet's own
 * attribution.  The panel cannot: its bottom-left corner is the Map
 * Type control.  It goes in the same corner instead, where Leaflet
 * stacks controls vertically, so the two lines sit above one another
 * rather than on top of one another.
 */
import type L from 'leaflet';

export interface CreditState {
  control: L.Control | null;
  /** Google's viewport service for the CURRENT session, or ''. */
  viewportUrl: string;
}

const GOOGLE_LABEL = 'Google';

/**
 * The query Google's viewport service takes.
 *
 * Latitude is clamped to ±85 because Web Mercator has no poles and the
 * service refuses anything beyond; longitude is not — a map scrolled
 * past the date line reports honest out-of-range values and the
 * service handles them.  Zoom is clamped to what the API accepts.
 */
export function viewportQuery(
  b: { north: number; south: number; east: number; west: number }, zoom: number,
): string {
  const lat = (v: number) => Math.max(-85, Math.min(85, v));
  return new URLSearchParams({
    zoom: String(Math.max(0, Math.min(22, Math.round(zoom)))),
    north: String(lat(b.north)), south: String(lat(b.south)),
    east: String(b.east), west: String(b.west),
  }).toString();
}

/** Put the line on the map, once. */
export function showCredit(map: L.Map, Leaf: typeof L, state: CreditState): void {
  if (state.control) return;
  const Ctl = Leaf.Control.extend({
    onAdd() {
      // Leaflet's own attribution class, so it inherits the strip
      // styling the panel already themes — one look for both lines.
      const el = Leaf.DomUtil.create('div', 'leaflet-control-attribution');
      el.setAttribute('aria-label', 'Google Maps');
      el.innerHTML = `<strong>${GOOGLE_LABEL}</strong> <span data-copyright></span>`;
      return el;
    },
  });
  const ctl = new Ctl({ position: 'bottomright' });
  ctl.addTo(map);
  state.control = ctl;
}

/** Take it off — the map stopped drawing Google. */
export function hideCredit(state: CreditState): void {
  state.control?.remove();
  state.control = null;
  state.viewportUrl = '';
}

/**
 * Ask Google what to print for the view on screen.
 *
 * Free of quota, and never blanks the line: a failed lookup keeps the
 * words that are already there rather than showing none, because an
 * empty attribution is worse than a slightly stale one.
 */
export async function refreshCredit(map: L.Map, state: CreditState): Promise<void> {
  const url = state.viewportUrl;
  const el = state.control?.getContainer()?.querySelector('[data-copyright]');
  if (!url || !el) return;
  const b = map.getBounds();
  const q = viewportQuery(
    { north: b.getNorth(), south: b.getSouth(), east: b.getEast(), west: b.getWest() },
    map.getZoom(),
  );
  try {
    const r = await fetch(`${url}&${q}`);
    if (!r.ok) return;
    const j = await r.json() as { copyright?: string };
    // The session may have been swapped while this was in the air; a
    // copyright for the previous one would be the wrong words.
    if (j.copyright && state.viewportUrl === url) el.textContent = j.copyright;
  } catch {
    /* keep the previous line */
  }
}
