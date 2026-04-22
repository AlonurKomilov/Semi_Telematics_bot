// ── User & Auth ──────────────────────────────────────────────

export interface Permissions {
  can_truck_all: boolean;
  can_truck_own: boolean;
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
  [key: string]: boolean;
}

export interface User {
  telegram_id: number;
  display_name: string;
  role: string;
  department?: string;
  account_id?: number;
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
  truck: string;
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
  my_truck?: {
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
  id: string | number;
  alert_key?: string;
  vehicle_name?: string;
  vehicle_id?: string;
  alert_type?: string;
  status?: string;
  created_at?: string;
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
  fuel_percent?: number;
  company?: string;
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

export interface GeofenceProperties {
  name?: string;
  radius?: number;
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

export interface AIChatMessage {
  role: 'user' | 'model';
  text: string;
}

export interface AIChatResponse {
  reply: string;
  suggestions: string[];
}

export interface AISummaryResponse {
  summary: string;
  suggestions: string[];
}

export interface AIDiagnoseResponse {
  diagnosis: string;
  vehicle: string;
}

export interface AIModel {
  name: string;
  display: string;
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
