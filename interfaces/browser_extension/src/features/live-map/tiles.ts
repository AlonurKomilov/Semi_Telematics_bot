/** Same tile sources as the dashboard's useLeafletMap. v2 adds Google here as a fourth entry. */
export type MapType = 'standard' | 'satellite' | 'terrain';
export const TILES: Record<MapType, { url: string; attr: string; maxZoom: number }> = {
  standard:  { url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
               attr: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 19 },
  satellite: { url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
               attr: 'Tiles &copy; Esri', maxZoom: 19 },
  terrain:   { url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
               attr: 'Map data: &copy; OpenStreetMap contributors, SRTM | &copy; OpenTopoMap', maxZoom: 17 },
};

/**
 * Where the map goes when OpenStreetMap stops answering.  Their tile
 * server rate-limits shared addresses — VPN exits, office NATs — with
 * 429/403, and Leaflet's only symptom is a grey map with the markers
 * still on it.  Same keyless Esri host the satellite layer already uses.
 */
export const FALLBACK = {
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
  attr: 'Tiles &copy; Esri', maxZoom: 19,
};
export const FALLBACK_AFTER_ERRORS = 4;
/**
 * Counts are per view (reset when the map moves).  A few errors while
 * tiles are still arriving is the internet; errors with as many or more
 * than the loads means THIS person cannot reach the source.
 */
export function shouldFallBack(errors: number, loads: number): boolean {
  return errors >= FALLBACK_AFTER_ERRORS && errors >= loads;
}
