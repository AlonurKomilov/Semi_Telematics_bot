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

export type Page = 'map' | 'vehicles' | 'pti' | 'alerts' | 'scorecard' | 'ai' | 'profile';

// ── PTI (Pre-Trip Inspection) ───────────────────────────────────────

export interface PTIItem {
  id: number;
  inspection_id: number;
  item_key: string;
  label: string;
  category: string;
  /** 'pending' | 'ok' | 'minor' | 'major' | 'oos' | 'na' */
  status: string;
  notes: string | null;
  requires_media: number; // 0 or 1
  required: number;       // 0 or 1
  sort_order: number;
  completed_at: string | null;
  /** 'check' (status buttons) | 'photo' (guided shot) | 'document' (upload). */
  item_type?: string;
  /** ObjectStorage filename of the reference example photo (null = none). */
  reference_image_url?: string | null;
}

export interface PTIMedia {
  id: number;
  inspection_id: number;
  item_id: number | null;
  media_type: 'photo' | 'video' | 'document';
  file_path: string;
  file_name: string;
  file_size: number;
  content_type: string;
  uploaded_by: number;
  uploaded_at: string;
  /** ISO timestamp when the driver baked annotations into the blob. */
  annotated_at?: string | null;
  /** Per-photo AI vision review (null = never checked). */
  ai_review_status?: string | null;   // 'completed' | 'error'
  ai_review_result?: string | null;    // JSON: {verdict, confidence, summary, model}
  ai_reviewed_at?: string | null;
  /** Hybrid-storage state: 'local' | 'syncing' | 'remote' | 'stuck'.
   *  Drives the small badge on the per-item thumbnail strip. */
  storage_state?: string | null;
}

export interface PTIInspection {
  id: number;
  account_id: number;
  user_id: number;
  vehicle_name: string;
  trailer_name: string | null;
  inspection_type: string;
  /** Workflow status. */
  status: 'scheduled' | 'in_progress' | 'submitted' | 'reviewed' | 'revision_required';
  /** Review outcome (null until reviewed). */
  review_status: 'approved' | 'needs_service' | 'rejected' | 'revision_required' | null;
  review_notes: string | null;
  scheduled_for: string | null;
  due_by: string | null;
  submitted_at: string | null;
  reviewed_at: string | null;
  reviewed_by: number | null;
  defects_count: number;
  has_oos_defect: number;
  template_id: number;
  template_version: number;
  /** Base64 PNG data URL — present once captured. */
  driver_signature?: string | null;
  driver_signed_at?: string | null;
  reviewer_signature?: string | null;
  reviewer_signed_at?: string | null;
  items?: PTIItem[];
  media?: PTIMedia[];
}
