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
