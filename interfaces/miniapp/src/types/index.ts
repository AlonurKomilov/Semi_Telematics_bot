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
  /** Current OBD odometer in miles (warehouse-sourced).  Null when the
   *  vehicle has no CAN bus gateway or hasn't reported yet. */
  odometer_miles: number | null;
  /** ISO timestamp of the odometer reading itself — distinct from
   *  ``time`` which tracks the location/state freshness. */
  odometer_time: string | null;
  status: 'moving' | 'idle' | 'stopped';
  /** Per-vehicle "as-of" timestamp from Samsara, ISO-8601.  Used by
   *  RelativeTime to render real signal freshness. */
  time?: string | null;
}

export interface MaintenanceTask {
  id: number;
  vehicle_name: string;
  task_type: string;
  description?: string;
  due_date: string | null;
  due_miles: number | null;
  /** Last odometer reading captured by the maintenance scheduler when
   *  it last ran the mileage check (warehouse-sourced).  Lets the UI
   *  show "247,500 / 250,000 mi (98%)" progress without an extra call. */
  last_odometer: number | null;
  status: 'pending' | 'overdue' | 'done' | string;
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
    def_percent: number | null;
    fault_count: number;
    address: string;
    engine_state: string;
    heading: number | null;
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

export type AlertSeverity = 'critical' | 'warning' | 'info';

export interface Alert {
  /** alert_history.id — the canonical AlertID surfaced in the UI ("#1234"). */
  id: number;
  alert_type: string;
  alert_key: string;
  vehicle_name: string;
  vehicle_id?: string;
  message: string;
  /** Server-authoritative severity — same value the bot used to format
   *  the Telegram message.  Frontends must NOT re-derive from alert_type. */
  severity?: AlertSeverity;
  /** Snapshot location string ("Mojave Freeway, CA") captured by the
   *  ingest pipeline; empty when the truck reported no GPS. */
  location?: string;
  /** First time this logical alert fired (also used by RelativeTime). */
  created_at: string;
  /** Most recent fire time — drives "× N occurrences" freshness display. */
  last_seen?: string;
  /** Total times this alert has fired without being cleared. */
  occurrence_count?: number;
  /** 'active' | 'cleared' | 'acknowledged'. */
  status?: string;
}

export type Page = 'map' | 'vehicles' | 'alerts' | 'scorecard' | 'ai' | 'profile';
