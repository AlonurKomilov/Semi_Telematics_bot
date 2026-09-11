/**
 * Same tile sources as the dashboard's useLeafletMap.
 *
 * NOT OpenStreetMap's servers, and not OpenTopoMap's, and that is the
 * whole point of this comment.  Both are volunteer-run, and both forbid
 * exactly what this is: "distributing an app that uses tiles from
 * openstreetmap.org" without prior permission.  A published Chrome
 * extension drawing a fleet map is squarely that, and on 2026-09-11 OSM
 * blocked us — the owner's map filled with tiles that say "403 Access
 * blocked" in the picture itself.
 *
 * Leaving OpenTopoMap behind for the same reason: keeping a second
 * volunteer server in a distributed app after the first one blocked us
 * is making the same mistake with a different host.  Esri's keyless
 * endpoints answer all three now.
 *
 * The paid path — Google Map Tiles, which permits Leaflet where the JS
 * API does not — is the next step and stays the owner's call on cost.
 */
export type MapType = 'standard' | 'satellite' | 'terrain';

/** The same three, as a list a stored value can be checked against —
 *  `getChoice` needs the set, not just the type, because a word written
 *  by an older build must not survive into a state nothing can draw. */
export const MAP_TYPES = ['standard', 'satellite', 'terrain'] as const;
export const MAP_ENGINES = ['osm', 'google'] as const;

/** What the picker calls each one.  "Standard" rather than "Esri": the
 *  TYPE is what the person is choosing here, and whose map it is belongs
 *  to the provider row beside it. */
export const MAP_TYPE_LABEL: Record<MapType, string> = {
  standard: 'Standard', satellite: 'Satellite', terrain: 'Terrain',
};
export const TILES: Record<MapType, { url: string; attr: string; maxZoom: number }> = {
  standard:  { url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
               attr: 'Tiles &copy; Esri', maxZoom: 19 },
  satellite: { url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
               attr: 'Tiles &copy; Esri', maxZoom: 19 },
  terrain:   { url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
               attr: 'Tiles &copy; Esri', maxZoom: 19 },
};

/**
 * Road names and city labels, drawn ON TOP of satellite or terrain.
 *
 * Not for `standard`: those tiles carry their own labels, and Google's
 * roadmap does too — switching it on there would print every name
 * twice.  Same Esri family as the bases, same keyless endpoint, same
 * layer the dashboard uses, so a map with labels reads identically on
 * both surfaces.
 */
export const LABELS = {
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/'
     + 'World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
  attr: 'Labels &copy; Esri',
  maxZoom: 19,
};

/**
 * Where the map goes when the chosen source stops answering.
 *
 * It is the SAME host as `standard` today, so for that layer it is
 * currently a no-op — said out loud rather than left as a mechanism that
 * quietly does nothing.  It stays because the default is due to move to
 * Google's paid tiles, and that is exactly when a keyless second source
 * earns its place again.
 *
 * And a warning for whoever reaches for it next: this net does NOT catch
 * being blocked.  A blocked client is served a PICTURE that says "403
 * Access blocked", with a successful HTTP status — so Leaflet fires
 * `tileload`, not `tileerror`, the counter below never moves, and the
 * map fills with error images while the code believes it is fine.  That
 * is how the OSM block went unnoticed.  Not depending on a server that
 * blocks distributed apps is the fix; this is not.
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
