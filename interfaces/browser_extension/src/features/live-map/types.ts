/** Wire shapes — copied from the dashboard's types, pinned by a test. */
export interface MapVehicleProperties {
  id?: string; name: string; address?: string; speed_mph?: number;
  engine_state?: string; status?: 'moving' | 'idle' | 'stopped' | string;
  fuel_percent?: number; def_percent?: number; fault_count?: number;
  company?: string; heading?: number | null; updated_at?: string;
  /** Who supplies this record: `source` is the creator,
   *  `sources` is creator-then-enrichers. */
  source?: string; sources?: string[];
}
export interface MapVehicleFeature {
  type: 'Feature';
  geometry: { type: 'Point'; coordinates: [number, number] };
  properties: MapVehicleProperties;
}
export interface MapVehiclesResponse { type: 'FeatureCollection'; features: MapVehicleFeature[]; }
export interface LiveVehiclePosition { lat: number; lng: number; speed_mph: number; heading: number | null; updated_at: string; }
export interface LiveVehiclesResponse { positions: Record<string, LiveVehiclePosition>; }
export type VehicleStatus = 'moving' | 'idle' | 'stopped';
