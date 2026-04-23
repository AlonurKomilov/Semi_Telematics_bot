// Shared TypeScript types for the miniapp

export interface Vehicle {
  id: string;
  name: string;
  company: string;
  latitude: number | null;
  longitude: number | null;
  speed_mph: number | null;
  address: string;
  engine_state: string;
  fuel_percent: number | null;
  def_percent: number | null;
  fault_count: number;
  status: 'moving' | 'idle' | 'stopped';
}

export interface VehicleFeature {
  type: 'Feature';
  geometry: {
    type: 'Point';
    coordinates: [number, number]; // [lng, lat]
  };
  properties: {
    id: string;
    name: string;
    company: string;
    status: 'moving' | 'idle' | 'stopped';
    speed_mph: number | null;
    fuel_percent: number | null;
    address: string;
    engine_state: string;
    updated_at: string | null;
  };
}

export interface GeofenceFeature {
  type: 'Feature';
  geometry:
    | { type: 'Polygon'; coordinates: [number, number][][] }
    | { type: 'Point'; coordinates: [number, number] };
  properties: {
    id: string;
    name: string;
    type: 'polygon' | 'circle';
    radius_meters?: number;
  };
}

export interface Alert {
  id: number;
  alert_type: string;
  alert_key: string;
  vehicle_name: string;
  message: string;
  created_at: string;
}

export type Page = 'map' | 'trucks' | 'alerts';
