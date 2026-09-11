/**
 * Putting ONE base layer on the map, and taking the last one off.
 *
 * It lives here rather than inside the panel because the panel is large
 * and this is the part with the ordering hazard: two swaps can be in
 * flight at once — a person pressing Satellite then Terrain before the
 * first session answers — and the slower one must not win.  A sequence
 * number settles it, and keeping the whole rule in one file is what lets
 * it be read in one sitting.
 */
import type * as L from 'leaflet';

import { GOOGLE_TYPE, tileSession, type MapEngine } from './engine';
import { LABELS, TILES, type MapType } from './tiles';

export interface BaseState {
  /** The layer currently on the map, so the next swap can remove it. */
  layer: L.TileLayer | null;
  /** The road-names overlay, when it is switched on.  Held separately
   *  because it survives a base swap conceptually — the person asked
   *  for names, not for names ON Satellite — but has to be re-added
   *  after one, since the new base would otherwise cover it. */
  labels: L.TileLayer | null;
  /** Raised on every swap; a resolved session with a stale number is
   *  dropped rather than drawn over a newer choice. */
  seq: number;
}

export interface SwapResult {
  /** What was actually drawn.  'osm' when Google was asked for and could
   *  not answer — the caller says so rather than leaving a blank map. */
  drew: MapEngine;
  /** Google's per-viewport copyright endpoint, when Google was drawn. */
  viewportUrl: string;
}

/**
 * Draw `type` from `provider`, replacing whatever is there.
 *
 * Google is asked for a session first.  No session means a spent quota,
 * an account without the engine, or no network — all of which are "we
 * cannot draw Google right now", and all of which are answered the same
 * way: draw the free layer.  A picker that silently did nothing would be
 * worse than one that quietly delivers the other map and says so.
 */
export async function applyBase(
  map: L.Map, Leaf: typeof L, state: BaseState,
  type: MapType, provider: MapEngine, showLabels = false,
): Promise<SwapResult> {
  const mine = ++state.seq;

  let layer: L.TileLayer | null = null;
  let drew: MapEngine = 'osm';
  let viewportUrl = '';

  if (provider === 'google') {
    const sess = await tileSession(GOOGLE_TYPE[type]);
    if (mine !== state.seq) return { drew: 'google', viewportUrl: '' };  // superseded
    if (sess) {
      layer = Leaf.tileLayer(sess.tile_url, {
        maxZoom: sess.max_zoom || 22,
        tileSize: sess.tile_size || 256,
        // Google's line is a viewport answer, not a fixed string, so the
        // caller renders it; Leaflet's own attribution stays empty rather
        // than showing a credit that would be wrong half the time.
        attribution: '',
      });
      drew = 'google';
      viewportUrl = sess.viewport_url;
    }
  }

  if (!layer) {
    const t = TILES[type];
    layer = Leaf.tileLayer(t.url, { attribution: t.attr, maxZoom: t.maxZoom });
  }

  if (mine !== state.seq) return { drew, viewportUrl };   // superseded while building
  state.layer?.remove();
  layer.addTo(map);
  state.layer = layer;
  applyLabels(map, Leaf, state, type, showLabels);
  return { drew, viewportUrl };
}

/**
 * Put the road-names overlay on, or take it off.
 *
 * Exported as well as called from the swap, because the two callers ask
 * different questions: the swap asks "the base changed, restore what
 * was asked for", the toggle asks "they just changed their mind".  One
 * function, so the pane and the standard-tiles exception are written
 * once.
 *
 * `shadowPane` is the pane between tiles and markers — the same one the
 * dashboard uses.  Named here because it looks arbitrary: it is the one
 * built-in pane that sits above the base tiles and below every marker,
 * so labels never cover a truck.
 */
export function applyLabels(
  map: L.Map, Leaf: typeof L, state: BaseState, type: MapType, show: boolean,
): void {
  state.labels?.remove();
  state.labels = null;
  // Standard carries its own names, and so does Google's roadmap.
  if (!show || type === 'standard') return;
  state.labels = Leaf.tileLayer(LABELS.url, {
    attribution: LABELS.attr, maxZoom: LABELS.maxZoom, pane: 'shadowPane',
  }).addTo(map);
}
