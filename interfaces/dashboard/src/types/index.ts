// ── User & Auth ──────────────────────────────────────────────

export interface Permissions {
  can_vehicle_all: boolean;
  can_vehicle_own: boolean;
  can_location_map: boolean;
  can_location_own: boolean;
  can_alerts_all: boolean;
  can_alerts_own: boolean;
  can_geofence_all: boolean;
  can_geofence_own: boolean;
  can_route_all: boolean;
  can_route_own: boolean;
  can_scorecard_all: boolean;
  can_scorecard_own: boolean;
  can_events_all: boolean;
  can_events_own: boolean;
  can_faults: boolean;
  can_health: boolean;
  can_fuel: boolean;
  can_fuel_cost: boolean;
  can_cost_per_mile: boolean;
  can_maintenance_all: boolean;
  can_maintenance_own: boolean;
  can_manage_users: boolean;
  can_manage_companies: boolean;
  can_manage_account: boolean;
  can_manage_billing: boolean;
  can_invite: boolean;
  can_critical: boolean;
  can_efficiency: boolean;
  can_rolling_stopped: boolean;
  can_digest: boolean;
  can_manage_poi_layers: boolean;
  can_payroll_admin: boolean;
  can_payroll_view_own: boolean;
  can_coaching_admin: boolean;
  can_coaching_view_own: boolean;
  [key: string]: boolean;
}

export interface User {
  telegram_id: number;
  display_name: string;
  role: string;
  department?: string;
  account_id?: number;
  payroll_enabled?: boolean;
  coaching_enabled?: boolean;
  truck_num?: string;
  trucks?: string[];
  allowed_companies?: string[];
  language?: string;
  timezone?: string;
  quiet_start?: number;
  quiet_end?: number;
  permissions: Permissions;
}

export interface TelegramLoginData {
  id: number;
  first_name: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
  auth_date: number;
  hash: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
}

// ── Fleet / Vehicles ─────────────────────────────────────────

export interface Vehicle {
  id?: string;
  name: string;
  vin?: string;
  make?: string;
  model?: string;
  year?: number;
  licensePlate?: string;
  license_plate?: string;
  engineState?: string;
  engine_state?: string;
  fuelPercent?: number;
  fuel_percent?: number;
  defPercent?: number;
  def_percent?: number;
  speed_mph?: number;
  status?: string;
  company?: string;
  _org?: string;
  address?: string;
  formattedAddress?: string;
  latitude?: number;
  longitude?: number;
  fault_count?: number;
  faults?: Fault[];
  fault_codes?: Fault[];
  location?: VehicleLocation;
  /** Current OBD odometer in miles (warehouse-sourced).  Null when the
   *  vehicle has no CAN bus gateway or hasn't reported yet. */
  odometer_miles?: number | null;
  /** ISO timestamp of the odometer reading itself — distinct from
   *  the location ``time`` which tracks GPS/state freshness. */
  odometer_time?: string | null;
}

export interface VehicleLocation {
  latitude?: number;
  longitude?: number;
  reverseGeo?: {
    formattedLocation?: string;
  };
}

export interface VehiclesResponse {
  vehicles: Vehicle[];
}

// ── Health ───────────────────────────────────────────────────

export interface HealthData {
  battery_v?: number;
  oil_psi?: number;
  coolant_c?: number;
  def_pct?: number;
  load_pct?: number;
  rpm?: number;
  seatbelt?: string;
}

export interface HealthResponse {
  health: HealthData;
  alerts: string[];
}

// ── Faults ───────────────────────────────────────────────────

export interface J1939Info {
  spnDescription?: string;
  fmiDescription?: string;
  txId?: number;
  sourceAddressName?: string;
}

export interface Fault {
  j1939?: J1939Info;
  spnDescription?: string;
  code?: string;
  description?: string;
  occurrences?: number;
}

export interface FaultsResponse {
  vehicle: string;
  faults: Fault[];
}

// ── Dashboard Stats ──────────────────────────────────────────

export interface FleetStats {
  total?: number;
  moving?: number;
  idle?: number;
  stopped?: number;
}

export interface DashboardStats {
  role?: string;
  fleet: FleetStats;
  faults?: number;
  low_fuel?: number;
  pending_alerts?: number;
  unsafe_parking?: number;
  unknown_parking?: number;
  maintenance_due?: number;
  // Driver-specific
  truck_num?: string;
  my_vehicle?: {
    name: string;
    status: string;
    speed_mph: number;
    fuel_pct: number | null;
    location: string;
    faults: number;
    company: string;
  };
  my_alerts?: number;
}

// ── Alerts ───────────────────────────────────────────────────

export interface Alert {
  /** alert_history.id — canonical AlertID; "#1234" in the UI. */
  id: string | number;
  alert_key?: string;
  vehicle_name?: string;
  vehicle_id?: string;
  alert_type?: string;
  status?: string;
  /** First time this logical alert fired — used for "since 06:47" display. */
  created_at?: string;
  /** Most recent fire — drives the "X minutes ago" freshness pill. */
  last_seen?: string;
  /** Total times this alert has fired without being cleared.
   *  Rendered as "× 5" badge on the alert card. */
  occurrence_count?: number;
  /** Latest description line — already populated by the server in `message`. */
  last_detail?: string;
  message?: string;
  acknowledged_by?: number;
  acknowledged_at?: string;
}

export interface AlertsResponse {
  alerts: Alert[];
  count: number;
}

export interface BulkAckResponse {
  acked: number;
  failed: number;
  total: number;
}

// ── Map / GeoJSON ────────────────────────────────────────────

export interface MapVehicleProperties {
  id?: string;
  name: string;
  address?: string;
  speed_mph?: number;
  engine_state?: string;
  // Authoritative status computed server-side from CAN-bus engineStates +
  // speed (see capabilities/location/service.py).  LiveMap reads this
  // directly; the local heuristic is a fallback for old payloads/tests.
  status?: 'moving' | 'idle' | 'stopped' | string;
  fuel_percent?: number;
  def_percent?: number;
  company?: string;
  heading?: number | null;
  updated_at?: string;
}

export interface MapVehicleFeature {
  type: 'Feature';
  geometry: {
    type: 'Point';
    coordinates: [number, number];
  };
  properties: MapVehicleProperties;
}

export interface MapVehiclesResponse {
  type: 'FeatureCollection';
  features: MapVehicleFeature[];
}

/** Returned by /map/vehicles/live — position-only fast update. */
export interface LiveVehiclePosition {
  lat: number;
  lng: number;
  speed_mph: number;
  heading: number | null;
  updated_at: string;
}

export interface LiveVehiclesResponse {
  positions: Record<string, LiveVehiclePosition>;
}

export interface GeofenceProperties {
  id?: number;
  name?: string;
  radius?: number;
  radius_meters?: number;
  type?: string;
  geofence_type?: string;
  zone_role?: string;
  company?: string;
  source?: 'samsara' | 'platform';
}

export interface GeofenceFeature {
  type: 'Feature';
  geometry: {
    type: 'Point' | 'Polygon';
    coordinates: [number, number] | [number, number][][];
  };
  properties: GeofenceProperties;
}

export interface GeofencesResponse {
  type: 'FeatureCollection';
  features: GeofenceFeature[];
}

// ── DataTable ────────────────────────────────────────────────

export interface Column<T = Record<string, unknown>> {
  key: string;
  label: string;
  sortable?: boolean;
  render?: (value: unknown, row: T) => React.ReactNode;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type AnyColumn = Column<any>;

// ── Routes ─────────────────────────────────────────────

export interface RoutePoint {
  lat: number;
  lng: number;
  speed_mph: number;
  time: string;
}

export interface RouteReplayResponse {
  vehicle: string;
  date: string;
  points: RoutePoint[];
  point_count: number;
  total_miles: number;
  max_speed_mph: number;
}

export interface DispatchVehicle {
  id?: string;
  name: string;
  company?: string;
}

export interface DispatchVehiclesResponse {
  vehicles: DispatchVehicle[];
}

// ── Safety & Compliance ──────────────────────────────────────

export interface Scorecard {
  driver_id: string;
  driver_name: string;
  company: string;
  miles: number;
  mpg: number;
  drive_hours: number;
  idle_hours: number;
  drive_pct: number;
  idle_pct: number;
  eco_pct: number;
  overspeed_min: number;
  coast_min: number;
  cruise_min: number;
  anticipatory_braking_pct: number;
}

export interface ScorecardsResponse {
  scorecards: Scorecard[];
  count: number;
  days: number;
}

// ── Composite (feature-driven) scorecards ────────────────────

export interface ScoreEventBreakdown {
  rule_id: string;
  label: string;
  category: string;       // 'safety' | 'fleet' | 'efficiency' | … (legacy UI grouping)
  pillar?: PillarKey;     // 'safety' | 'efficiency' | 'compliance' (new)
  kind: string;           // 'bonus' | 'penalty'
  points: number;         // signed
  occurrences: number;
  source: string;         // 🅢 / 🅘 / 🅜 badge
}

// ── Audit Option C: pillar-aware shape ─────────────────────────

export type PillarKey = 'safety' | 'efficiency' | 'compliance';

export interface PillarSummary {
  cap: number;            // pillar budget (50/25/25)
  subtotal: number;       // 0..cap, clamped
  bonus_total: number;
  penalty_total: number;  // signed (≤ 0)
  events: ScoreEventBreakdown[];
  has_data: boolean;      // false ⇒ render "n/a"
}

export interface RuleCurve {
  curve_kind: string | null;
  curve_x_zero: number | null;
  curve_x_max:  number | null;
  curve_y_max:  number | null;
}

export interface CompositeScorecard {
  driver_id: string;
  driver_name: string;
  /** Phase B — canonical subject identity.  ``driver_id``/``driver_name``
   *  are kept as aliases that always carry the same value. */
  subject_id?: string;
  subject_name?: string;
  subject_type?: 'driver' | 'vehicle';
  company: string;
  /** Phase F (driver-inline) — populated only when ``subject_type``
   *  is ``vehicle``.  Samsara-paired driver wins; ``assigned_driver_name``
   *  is the manual fallback from the per-account driver→truck map. */
  paired_driver_name?: string | null;
  assigned_driver_name?: string | null;
  /** Phase F (sparkline) — last ~14 daily totals (oldest → newest)
   *  pulled from ``daily_scorecard_snapshots``.  Empty array when the
   *  subject has no snapshots yet (new tenants, or trucks added mid-window). */
  score_trend?: number[];
  // ── New canonical fields (Audit Option C) ─────────────────
  total?: number;                                 // 0-100, sum of pillar subtotals
  pillars?: Record<PillarKey, PillarSummary>;     // optional during one-release transition
  exposure?: {
    miles: number;
    drive_hours: number;
    idle_hours: number;
  };
  insufficient_data?: boolean;
  // ── Legacy aliases (kept for one release) ─────────────────
  score: number;          // = total when new shape present
  base: number;           // 0 in new shape
  bonus_total: number;
  penalty_total: number;
  bonuses: ScoreEventBreakdown[];
  penalties: ScoreEventBreakdown[];
  inputs: {
    miles: number;
    mpg: number;
    drive_hours: number;
    idle_hours: number;
    drive_pct: number;
    idle_pct: number;
    eco_pct: number;
    overspeed_min: number;
    coast_min: number;
    cruise_min: number;
    anticipatory_braking_pct: number;
    _source: string;
  };
}

export interface CompositeScorecardsResponse {
  scorecards: CompositeScorecard[];
  count: number;
  days: number;
  /** Phase B — echoes back the ?subject= the request used. */
  subject?: 'driver' | 'vehicle';
}

export interface ScoreHistoryPoint {
  date: string;
  score: number;
  /**
   * Audit Option C — present when the row was filtered to a specific
   * pillar (`?pillar=safety|efficiency|compliance`).  ``false`` means
   * the snapshot existed but the pillar didn't have enough exposure
   * data to produce a score.
   */
  has_data?: boolean;
}

export interface ScoreHistoryResponse {
  driver_id: string;
  /** Audit Option C — echoed when the request used `?pillar=`. */
  pillar?: 'safety' | 'efficiency' | 'compliance' | null;
  history: ScoreHistoryPoint[];
  count: number;
}

export interface SafetyEvent {
  event_id: string;
  event_type: string;
  severity: string;
  driver_id: string;
  driver_name: string;
  vehicle_id: string;
  vehicle_name: string;
  time: string;
  g_force: number;
  latitude?: number;
  longitude?: number;
  video_url: string;
  inward_video_url: string;
  coaching_state: string;
  company: string;
}

export interface EventsSummary {
  by_type: Record<string, number>;
  by_severity: Record<string, number>;
}

export interface SafetyEventsResponse {
  events: SafetyEvent[];
  count: number;
  days: number;
  summary: EventsSummary;
}

export interface CameraCheck {
  id: number;
  vehicle_id: string;
  vehicle_name: string;
  camera_type: string;
  status: string;
  obstruction: string;
  alignment: string;
  quality: string;
  summary: string;
  checked_at: string;
  image_path: string;
}

export interface CameraChecksResponse {
  checks: CameraCheck[];
  count: number;
}

// ── Reports ──────────────────────────────────────────────────

export interface FaultDTC {
  spn: number;
  spn_desc: string;
  fmi: number;
  fmi_desc: string;
  occurrences: number;
}

export interface FaultVehicle {
  vehicle_name: string;
  company: string;
  dtc_count: number;
  dtcs: FaultDTC[];
  lights: Record<string, boolean>;
  severity: string;
  fault_time: string;
}

export interface FaultReportResponse {
  vehicles: FaultVehicle[];
  total_vehicles: number;
  faulted_count: number;
}

export interface FuelVehicle {
  vehicle_name: string;
  company: string;
  fuel_pct: number | null;
  def_pct: number | null;
  fuel_time: string;
  def_time: string;
}

export interface FuelReportResponse {
  vehicles: FuelVehicle[];
  count: number;
  summary: {
    avg_fuel_pct: number | null;
    critical: number;
    low: number;
    good: number;
  };
}

export interface HealthVehicle {
  vehicle_name: string;
  company: string;
  battery_v: number | null;
  oil_psi: number | null;
  coolant_c: number | null;
  def_pct: number | null;
  load_pct: number | null;
  seatbelt: string | null;
  rpm: number | null;
  engine_on: boolean | null;
  alerts: string[];
}

export interface HealthReportResponse {
  vehicles: HealthVehicle[];
  count: number;
  alert_count: number;
}

export interface EfficiencyVehicle {
  vehicle_name: string;
  company: string;
  driver_name: string;
  miles: number;
  mpg: number;
  drive_hours: number;
  idle_hours: number;
  drive_pct: number;
  idle_pct: number;
  eco_pct: number;
  overspeed_min: number;
}

export interface EfficiencyReportResponse {
  vehicles: EfficiencyVehicle[];
  count: number;
  days: number;
}

// ── Costs ────────────────────────────────────────────────────

export interface FuelEntry {
  id?: number;
  vehicle_name: string;
  company_code: string;
  gallons: number;
  price_per_gallon: number;
  total_cost: number;
  odometer_miles: number;
  date: string;
  created_at?: string;
}

export interface FuelEntriesResponse {
  entries: FuelEntry[];
  count: number;
}

export interface FuelSummaryVehicle {
  vehicle_name: string;
  company: string;
  entries: number;
  total_gallons: number;
  total_cost: number;
  avg_price: number;
  first_odo: number | null;
  last_odo: number | null;
}

export interface FuelSummaryResponse {
  vehicles: FuelSummaryVehicle[];
  count: number;
  fleet_total_cost: number;
  fleet_total_gallons: number;
}

export interface CPMVehicle {
  vehicle_name: string;
  company: string;
  miles: number;
  total_cost: number;
  gallons: number;
  cpm: number;
  mpg: number;
}

export interface CPMResponse {
  vehicles: CPMVehicle[];
  count: number;
  fleet_avg_cpm: number;
  fleet_avg_mpg: number;
  fleet_total_miles: number;
  fleet_total_cost: number;
}

// ── Admin ────────────────────────────────────────────────────

export interface AdminUser {
  id: number;
  telegram_id: number;
  display_name: string;
  role: string;
  department: string;
  truck_num: string | null;
  trucks: string[];
  allowed_companies: string[];
  is_active: boolean;
  email: string | null;
  language: string | null;
  timezone: string | null;
  created_at: string | null;
}

export interface AdminUsersResponse {
  users: AdminUser[];
  count: number;
}

export interface InviteInfo {
  id: number;
  code: string;
  role: string;
  department: string;
  truck_num: string | null;
  expires_at: string;
  used_by: number | null;
  is_used: boolean;
  is_expired: boolean;
  created_by: number;
}

export interface InvitesResponse {
  invites: InviteInfo[];
  count: number;
}

export interface CompanyInfo {
  id: number;
  code: string;
  display_name: string;
  active_days: number;
  is_active: boolean;
  created_at: string;
  has_api_key: boolean;
}

export interface CompaniesResponse {
  companies: CompanyInfo[];
  count: number;
}

export interface AuditLogEntry {
  id: number;
  account_id: number;
  user_id: number;
  action: string;
  target_type: string;
  target_id: string;
  details: string;
  created_at: string;
}

export interface AuditLogResponse {
  entries: AuditLogEntry[];
  count: number;
}

export interface WorkSchedule {
  id: number;
  account_id: number;
  label: string;
  start_hour: number;
  end_hour: number;
  target_role: string;
  created_by: number;
}

export interface AccountInfo {
  id: number | null;
  name: string;
  tier: string;
  is_active: boolean;
}

export interface SettingsResponse {
  account: AccountInfo;
  settings: Record<string, string>;
  ai_usage: Record<string, unknown>;
  schedules: WorkSchedule[];
}

export interface BotConfig {
  has_bot: boolean;
  bot_username: string;
  bot_id?: number;
  first_name?: string;
  is_running?: boolean;
}

// ── Maintenance ──────────────────────────────────────────────

export interface MaintenanceTask {
  id: number;
  account_id: number;
  company_code: string;
  vehicle_id: string | null;
  vehicle_name: string;
  task_type: string;
  description: string;
  due_date: string | null;
  due_miles: number | null;
  /** Last odometer reading captured by the maintenance scheduler when
   *  it last ran the mileage check (warehouse-sourced).  Combined with
   *  ``due_miles`` to render the progress bar in the maintenance UI. */
  last_odometer: number | null;
  status: string;
  recur_interval_days: number | null;
  recur_interval_miles: number | null;
  created_at: string;
  updated_at: string;
}

export interface MaintenanceTasksResponse {
  tasks: MaintenanceTask[];
  count: number;
}

// ── Parking ──────────────────────────────────────────────────

export interface ParkingEvent {
  id: number;
  account_id: number;
  vehicle_id: string;
  vehicle_name: string;
  company_code: string;
  latitude: number;
  longitude: number;
  address: string;
  first_stopped: string;
  duration_hours: number;
  location_class: string;
  alert_level: string;
  ai_analysis: string;
  map_image_path: string;
  resolved: number;
  last_checked: string;
  created_at: string;
}

export interface ParkingEventsResponse {
  events: ParkingEvent[];
  count: number;
}

export interface ParkingStatsResponse {
  total_parked: number;
  unsafe: number;
  unknown: number;
  safe: number;
}

// ── AI Assistant ──────────────────────────────────────────────────

export interface AIUsage {
  prompt_tokens: number;
  reply_tokens: number;
  total_tokens: number;
  thinking_tokens?: number;
}

export interface AIChatMessage {
  role: 'user' | 'model';
  text: string;
  /** Client-side timestamp — not persisted to backend */
  timestamp?: Date;
  /** Token usage from the backend — only present on model messages */
  usage?: AIUsage;
}

export interface AIChatResponse {
  reply: string;
  suggestions: string[];
  usage?: AIUsage;
}

export interface AISummaryResponse {
  summary: string;
  suggestions: string[];
  usage?: AIUsage;
}

export interface AIDiagnoseResponse {
  diagnosis: string;
  vehicle: string;
}

export interface AIModel {
  name: string;
  display: string;
  description: string;
  category: string;
  vision: boolean;
  cost_per_request: number | null;
}

export interface AIModelsResponse {
  models: AIModel[];
  current_text: string;
  current_vision: string;
  account_default: string;
  is_admin: boolean;
}

export interface AIHistoryResponse {
  messages: AIChatMessage[];
  count: number;
}

// ── Report Subscriptions ────────────────────────────────────────────

export interface Subscription {
  id?: number;
  user_id?: number;
  frequency: string;
  report_type: string;
  send_hour: number;
  timezone: string;
  is_active?: number;
  created_at?: string;
}
