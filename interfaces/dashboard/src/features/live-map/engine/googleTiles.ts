/**
 * Google's basemap under Leaflet: the parts that are pure.
 *
 * The Map Tiles API hands out a tile URL per session and, per viewport,
 * the copyright line Google requires beside its name. Leaflet needs the
 * first as a template and the second as text. Both mappings live here
 * so the hook that wires them can be thin and the rules can be tested
 * without a map.
 */
import type { MapType } from '@/hooks/useLeafletMap';

import type { TileType } from './useMapEngine';

/** Our picker's three choices, in Google's words. Terrain is imagery
 *  Google requires a road layer on, which the server adds when it opens
 *  the session; here it is just the name. */
export const TILE_TYPE_FOR: Record<MapType, TileType> = {
  standard:  'roadmap',
  satellite: 'satellite',
  terrain:   'terrain',
};

/** Google's policy: the Google Maps logo where there is room, the text
 *  "Google Maps" where there is not. A map corner is where there is
 *  not. Kept apart from Leaflet's own attribution line, as the policy
 *  asks — two providers, two lines, neither overlapping the other. */
export const GOOGLE_ATTRIBUTION_LABEL = 'Google Maps';

/** What the viewport request wants, from what Leaflet knows. Zoom is
 *  rounded because Leaflet allows fractional zooms and Google does
 *  not. Latitudes are clamped to what Google's projection can show. */
export function viewportParams(bounds: {
  north: number; south: number; east: number; west: number;
}, zoom: number): Record<string, string> {
  const clamp = (v: number) => Math.max(-85, Math.min(85, v));
  return {
    zoom:  String(Math.max(0, Math.min(22, Math.round(zoom)))),
    north: String(clamp(bounds.north)),
    south: String(clamp(bounds.south)),
    east:  String(bounds.east),
    west:  String(bounds.west),
  };
}

/** The copyright line, as Google phrases it — or, when a viewport
 *  answer has not arrived yet, the least we may show. Never empty:
 *  attribution that flickers out between pans is attribution that is
 *  sometimes missing. */
export function copyrightText(viewportCopyright: string | null | undefined): string {
  const t = (viewportCopyright || '').trim();
  return t || `Map data ©${new Date().getFullYear()} Google`;
}

/** How many failed tiles in one view before Google is judged to be
 *  refusing (a spent quota answers every tile with 403) rather than
 *  one tile being slow. Mirrors the OSM → Esri fallback rule in
 *  tiles.ts: errors must also outnumber successes, or a flaky edge
 *  would drop a working map. */
export const GOOGLE_FALLBACK_AFTER_ERRORS = 4;
export function shouldAbandonGoogle(errors: number, loads: number): boolean {
  return errors >= GOOGLE_FALLBACK_AFTER_ERRORS && errors >= loads;
}
