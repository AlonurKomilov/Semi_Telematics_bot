// ── User & Auth ──────────────────────────────────────────────

export interface Permissions {
  can_vehicle_all: boolean;
  can_vehicle_vehicle: boolean;
  can_location_map: boolean;
  can_location_vehicle: boolean;
  can_alerts_all: boolean;
  can_alerts_vehicle: boolean;
  can_geofence_all: boolean;
  can_geofence_vehicle: boolean;
  can_route_all: boolean;
  can_route_vehicle: boolean;
  can_scorecard_all: boolean;
  can_scorecard_vehicle: boolean;
  can_manage_config_role: boolean;
  can_manage_config_all: boolean;
  can_events_all: boolean;
  can_events_vehicle: boolean;
  can_faults: boolean;
  can_health: boolean;
  can_fuel: boolean;
  can_fuel_cost: boolean;
  can_cost_per_mile: boolean;
  can_maintenance_all: boolean;
  can_maintenance_vehicle: boolean;
  can_cost_reports: boolean;
  can_manage_users: boolean;
  can_manage_companies: boolean;
  can_manage_account: boolean;
  can_manage_permissions: boolean;
  can_manage_integrations: boolean;
  can_manage_storage: boolean;
  can_manage_work_hours: boolean;
  can_manage_billing: boolean;
  can_invite: boolean;
  can_efficiency: boolean;
  can_digest: boolean;
  can_manage_poi_layers: boolean;
  can_driver_pay_admin: boolean;
  can_driver_pay_view_own: boolean;
  can_coaching_admin: boolean;
  can_coaching_view_own: boolean;
  can_manage_driver_docs: boolean;
  can_manage_drivers: boolean;
  can_driver_docs_own: boolean;
  [key: string]: boolean;
}

export interface User {
  /** Stable internal user.id PK — use this for ownership comparisons
   *  (KB articles, work orders, PTI media).  Survives Telegram
   *  re-linking; ``telegram_id`` doesn't. */
  id?: number;
  /** ``null`` for users who registered via email and haven't linked
   *  their Telegram account yet.  Anything that needs a guaranteed
   *  identifier should use ``id`` instead. */
  telegram_id: number | null;
  /** Set when the user has email + password sign-in enabled. */
  email?: string | null;
  /** ``false`` when an email is attached but the user hasn't clicked
   *  the verification link yet.  The dashboard surfaces a "verify your
   *  email" notice on the Sign-in methods panel when this is false. */
  email_verified?: boolean;
  display_name: string;
  role: string;
  /** Per-user manager tier (orthogonal to role).  A recruiter with
   *  ``is_manager`` gets the recruiting team-lead grants (invite recruiters,
   *  edit the carrier directory).  Drives the "Manager" badge + affordances. */
  is_manager?: boolean;
  /** True only for the PRIMARY (main) owner — the one who can create/remove
   *  co-owners and do destructive account actions.  Co-owners have
   *  role='owner' but this false. */
  is_primary_owner?: boolean;
  account_id?: number;
  payroll_enabled?: boolean;
  coaching_enabled?: boolean;
  /** Enabled department modules (Fleet/Dispatch/Safety/HR/Accounting);
   *  Core + Account are always on and not listed.  Drives module-aware
   *  sidebar filtering.  Absent → treat as all-on. */
  enabled_modules?: string[];
  truck_num?: string;
  trucks?: string[];
  allowed_companies?: string[];
  language?: string;
  /** Per-user override (may be blank — inherits account default). */
  timezone?: string;
  /** Account-level default — read-only on /user/me. */
  account_timezone?: string;
  /** Resolved tz the dashboard should actually format dates in. */
  effective_timezone?: string;
  /** Working Hours ACTIVE window for this user — admin-managed since
   *  migration 100.  Both null → user inherits the role-level
   *  Working Hours.  Both set → admin assigned a personal override.
   *  The user sees these read-only on Profile alongside the DND
   *  toggle below; editing happens via Team Management. */
  quiet_start?: number;
  quiet_end?: number;
  /** Personal DND toggle (migration 100).  True → user honours the
   *  Working Hours schedule (non-critical alerts queue outside).
   *  False → user receives all non-critical alerts 24/7.
   *  Critical-severity alerts always deliver regardless. */
  dnd_enabled?: boolean;
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
  /** Login endpoints return a slim user envelope so the client can
   * role-route immediately without a follow-up /user/me roundtrip. */
  user?: {
    telegram_id?: number;
    name?: string;
    role?: string;
    account_id?: number;
  };
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
  /** Raw Samsara fuel reading — ``time`` is the reading's own clock
   *  (a fuel sensor can be days staler than GPS on the same truck). */
  fuel?: { value?: number | null; time?: string };
  /** Raw Samsara DEF reading (already scaled to percent). */
  def_level?: { value?: number | null; time?: string };
  /** Current OBD odometer in miles (warehouse-sourced).  Null when the
   *  vehicle has no CAN bus gateway or hasn't reported yet. */
  odometer_miles?: number | null;
  /** ISO timestamp of the odometer reading itself — distinct from
   *  the location ``time`` which tracks GPS/state freshness. */
  odometer_time?: string | null;
  /** Cumulative OBD engine hours (warehouse-sourced).  Null when the
   *  vehicle doesn't report ``obdEngineSeconds`` — usually means no
   *  CAN bus gateway or the Samsara plan doesn't include the signal. */
  engine_hours?: number | null;
  /** ISO timestamp of the engine-hours reading. */
  engine_hours_time?: string | null;
  /** Registry classification — drives the Type column.  'truck' for
   *  any live row the registry overlay didn't tag. */
  vehicle_type?: string;
  /** Where the row came from: 'manual' (operator added), 'samsara'
   *  (synced from telematics), 'datatruck' (Phase 2). */
  source?: string;
  /** Row-level freshness — freshest known reading time (GPS, else
   *  fuel/DEF) emitted by the list endpoint's _simplify. */
  time?: string;
  /** Registry row id for the manage UI's edit/delete.  Null for a
   *  live-only vehicle the registry hasn't caught yet. */
  registry_id?: number | null;
}

export interface VehicleLocation {
  latitude?: number;
  longitude?: number;
  reverseGeo?: {
    formattedLocation?: string;
  };
  /** ISO timestamp of the GPS fix — freshness of address/speed/coords. */
  time?: string;
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
  /** Per-stat Samsara clocks — each sensor reading carries its own
   *  timestamp (a dead sensor freezes independently of the others). */
  battery_time?: string;
  oil_time?: string;
  coolant_time?: string;
  def_time?: string;
  load_time?: string;
  rpm_time?: string;
  seatbelt_time?: string;
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

/** One per-type bucket of the vehicle counts.  Motion states count
 *  TRACKED units only — a registry vehicle with no telematics (most
 *  trailers, some trucks) is `no_signal`, never "stopped". */
export interface VehicleTypeStats {
  total: number;
  moving: number;
  idle: number;
  stopped: number;
  no_signal: number;
}

/** ROLE-NEUTRAL by design: Vehicle is the parent of truck and trailer.
 *  Deliberately NOT named "fleet" — in this codebase "fleet" is a ROLE
 *  (fleet.4truck.us, FleetShell); role-flavored UI words ("Fleet
 *  Overview" vs "Safety Overview") are generated from the active view,
 *  never hardcoded.  /overview/stats serves this same object to EVERY
 *  role; only sibling fields like `faults`/`low_fuel` are
 *  permission-gated. */
export interface VehicleStats {
  total?: number;
  moving?: number;
  idle?: number;
  stopped?: number;
  no_signal?: number;
  trucks?: VehicleTypeStats;
  trailers?: VehicleTypeStats;
}

export interface DashboardStats {
  role?: string;
  vehicles?: VehicleStats;
  /** Deprecated alias of `vehicles` (pre-rename API); read via
   *  `stats.vehicles ?? stats.fleet` until the alias is removed. */
  fleet?: VehicleStats;
  faults?: number;
  low_fuel?: number;
  pending_alerts?: number;
  /** ISO first_seen of the OLDEST still-open critical in this view, or
   *  absent when there is none.  A count says how much is open; this says
   *  how long the worst of it has waited, which is the number that should
   *  drive triage order. */
  oldest_critical_first_seen?: string;
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

export type AlertSeverity = 'critical' | 'warning' | 'info';

export interface Alert {
  /** alert_history.id — canonical AlertID; "#1234" in the UI. */
  id: string | number;
  alert_key?: string;
  vehicle_name?: string;
  vehicle_id?: string;
  /** Company code of the unit's company — set only on multi-company
   *  accounts (server tags /pending rows) so a row can show which company
   *  a unit belongs to.  '' / undefined → no company chip. */
  company?: string;
  alert_type?: string;
  /** Per-row kind within the type, when the server can derive one from
   *  the stored row ("braking", "unsafe", …).  '' / undefined → the UI
   *  falls back to the generic type label. */
  kind?: string;
  /** Server-authoritative severity — written by pipeline.send_alert.
   *  Frontends must NOT re-derive from alert_type. */
  severity?: AlertSeverity;
  /** Snapshot location ("Mojave Freeway, CA") captured at first fire. */
  location?: string;
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
  /** Telegram id of the actor who acked; >0 = a human, null/0 = auto-resolved. */
  acknowledged_by?: number | null;
  acknowledged_at?: string | null;
  /** Resolved display name for acknowledged_by (server LEFT JOIN). Blank when auto-resolved. */
  acknowledged_by_name?: string;
}

export interface AlertsResponse {
  alerts: Alert[];
  count: number;
  page?: number;
  page_size?: number;
  total_pages?: number;
}

export interface VehicleAlertGroup {
  vehicle_id: string;
  vehicle_name: string;
  alerts: Alert[];
  alert_count: number;
  critical_count: number;
  warning_count: number;
  info_count: number;
  latest_seen: string;
}

export interface VehiclesAlertsResponse {
  vehicles: VehicleAlertGroup[];
  /** Total number of *vehicles* (not alerts) — drives the per-vehicle pagination footer. */
  count: number;
  page?: number;
  page_size?: number;
  total_pages?: number;
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
  /** Count of active diagnostic trouble codes (DTCs) on this truck.
   *  Populated by /maps/vehicles from Samsara's activeFaultCodes.
   *  Used by the Fleet persona's FaultMarkersLayer overlay to ring
   *  trucks with active mechanical faults. */
  fault_count?: number;
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

// ── DataGrid ────────────────────────────────────────────────

/** Aggregation functions offered in the column ⋮ menu.  ``count`` is
 *  MUI's ``size`` — it counts rows and needs no numeric accessor; the
 *  other four reduce over ``aggValue(row)`` (or the raw cell number). */
export type AggFn = 'sum' | 'avg' | 'min' | 'max' | 'count';

/** Display names for the aggregation functions — shared by the ⋮-menu
 *  submenu and the under-header micro-label so they never drift. */
export const AGG_FN_LABELS: Record<AggFn, string> = {
  sum: 'Sum',
  avg: 'Average',
  min: 'Min',
  max: 'Max',
  count: 'Count',
};

/** Canonical order the functions appear in the menu. */
export const AGG_FN_ORDER: readonly AggFn[] = ['sum', 'avg', 'min', 'max', 'count'];

export interface Column<T = Record<string, unknown>> {
  key: string;
  label: string;
  sortable?: boolean;
  /** When true the column header renders an inline text-filter input
   *  alongside the sort chevron.  Substring match, case-insensitive,
   *  composes with other column filters (AND) and the global search.
   *  Off by default — opt in per column (skip for free-text fields
   *  like Description where the global search is the better tool). */
  filterable?: boolean;
  /** Filter UI shape.
   *   * ``'select'`` (default) — multi-select checkbox dropdown, for
   *     enum-like columns (Type / Company / Status).
   *   * ``'range'`` — Min / Max number-input pair, for continuous
   *     numeric columns (Fuel % / Odometer miles).
   *   * ``'date-range'`` — From / To native date-input pair, for
   *     date/timestamp columns (Submitted / Due date / Updated).
   *     Filter value shape ``[isoStart|null, isoEnd|null]`` with
   *     YYYY-MM-DD strings; the "To" bound is inclusive to end-of-day
   *     so a filter like "2025-11-15 → 2025-11-15" keeps rows from
   *     that whole day. */
  filterMode?: 'select' | 'range' | 'date-range';
  /** For ``filterMode: 'select'`` — the option list, declared instead of
   *  derived from the loaded rows.
   *
   *  Needed whenever the grid does NOT hold the whole set: options are
   *  normally collected from the rows in hand, so on a server-filtered
   *  grid picking "Fault" leaves only faults loaded and the menu
   *  collapses to the one value you already chose — with no way back.
   *  A fixed enum (alert types, severities, statuses) should just say so.
   *  Counts are omitted for declared options: the grid can't count what
   *  it wasn't given. */
  filterOptions?: Array<{ value: string; label: string }>;
  /** For ``filterMode: 'range'`` — optional bounds and unit.  When
   *  omitted, min/max are auto-computed from the data (whatever's
   *  currently loaded).  ``step`` drives the browser's number-input
   *  ↑↓ arrows; ``unit`` (``'%'`` / ``'mi'`` / ``'h'``) is appended
   *  to labels in the filter popover. */
  filterRange?: {
    min?: number;
    max?: number;
    step?: number;
    unit?: string;
  };
  /** Optional accessor for the value the column filter MATCHES on.
   *  Defaults to the cell value at ``key`` stringified.  Use for
   *  columns whose ``render`` produces something different from the
   *  raw row value (e.g. a Role column rendering a styled badge but
   *  filtering against the plain role string). */
  filterValue?: (row: T) => string;
  /** Optional accessor for the human-readable LABEL shown in the
   *  filter dropdown.  Distinct from ``filterValue`` so the column
   *  can match on internal codes (``oil``) but display friendly
   *  names (``Oil Change``).  Defaults to ``filterValue`` (or the
   *  raw cell value) when omitted — backwards-compatible.
   *  Receives the row object so it can use the same lookup logic
   *  the cell renderer uses. */
  filterLabel?: (row: T) => string;
  /** Optional accessor for the CSV-export value of this cell.  Lets
   *  badge-rendered columns export their plain-text equivalent
   *  ("Critical" instead of a coloured pill).  Defaults to
   *  ``filterLabel`` → ``filterValue`` → raw cell value, so columns
   *  that already opted into filtering get sensible CSV output for
   *  free. */
  csvValue?: (row: T) => string;
  /** Optional rich header — when set, the column header renders this
   *  React node instead of the plain ``label`` text.  Used for the
   *  Tasks bulk-select column to render a "select all visible" master
   *  checkbox in its header. */
  headerRender?: () => React.ReactNode;
  /** When true, the column is treated as structural rather than data:
   *  always visible, always at the left edge (auto-pinned), and the
   *  3-dot menu / drag handle are suppressed.  Use for UI-affordance
   *  columns like a bulk-select checkbox where the operator should
   *  never be able to hide / move / unpin it. */
  locked?: boolean;
  /** When true, the column starts HIDDEN in the DataGrid's initial
   *  render.  Operators can unhide it from the Columns popover (and
   *  their choice persists per-user via useUserPreference).  Use for
   *  secondary breakdown columns (City / State parsed out of a full
   *  Location address, extra breakdowns of a compound field) that
   *  should exist as filter surfaces without cluttering the default
   *  view.  Reset-to-defaults returns to hidden. */
  defaultHidden?: boolean;
  /** Floor for this column's width, in px.  The grid's global floor is
   *  60px — enough for an ellipsized label plus the ⋮ menu, but not for
   *  content with a KNOWN minimum shape: a date needs ~96px or it
   *  renders "2026-0…", and a header clipped to "DE…" names nothing.
   *  Set it where the content type has a real floor; leave it off and
   *  the column stays freely resizable from 60px. */
  minWidth?: number;
  /** Visual group label.  CONSECUTIVE visible columns sharing the
   *  same ``group`` string get one spanning label cell in an extra
   *  header row above the normal column headers (e.g. Street / City /
   *  State bracketed under "Location").  Purely presentational — the
   *  underlying columns keep their own sort / filter / pin / hide /
   *  drag behaviour.  If the operator reorders or pins a member away
   *  from its siblings, the label simply renders over each remaining
   *  contiguous run. */
  group?: string;
  render?: (value: unknown, row: T) => React.ReactNode;
  /** Custom comparable value for sorting.  When set, the table sorts
   *  by ``sortKey(row)`` instead of the raw cell value.  Use for
   *  enum-style columns (priority, status) where alphabetical order
   *  doesn't match operator expectations. */
  sortKey?: (row: T) => number | string;
  /** Opt this column into AGGREGATION — a total (sum/avg/min/max/count)
   *  rendered in a footer row.  Explicit by design: we have no column
   *  type system, so a column declares that its values are meaningfully
   *  combinable (money, mileage, hours, counts) rather than the grid
   *  guessing.  The operator picks the function from the ⋮ menu; the
   *  choice persists per-user. */
  aggregable?: boolean;
  /** What kind of values this column aggregates — drives which functions
   *  are offered and how the result is read + formatted:
   *   * ``'number'`` (default) — sum/avg/min/max/count over numbers.
   *   * ``'date'`` — min (earliest) / max (latest) / count over
   *     dates/timestamps.  The menu hides sum/avg (meaningless on dates),
   *     ``aggValue`` may return a Date / ISO string / ms number, and the
   *     footer formats the result as a day (override with ``aggFormat``).
   *     A bare ``YYYY-MM-DD`` value is treated as a calendar day (shown
   *     tz-neutrally); a full timestamp is an instant (its day shown in
   *     the account timezone). */
  aggType?: 'number' | 'date';
  /** Which functions the ⋮ menu offers for this column.  Defaults by
   *  ``aggType``: number → all five; date → ``['min','max','count']``.
   *  Set it to narrow further. */
  aggFns?: AggFn[];
  /** Value the aggregation reduces over — defaults to the raw cell value
   *  at ``key`` (coerced to a number for ``'number'`` columns, parsed as
   *  a date for ``'date'`` columns).  Set it when the cell renders
   *  something formatted (``"$2,847"``) but the true value lives
   *  elsewhere on the row (``row.gross_cents``).  For a date column it
   *  may return a Date, an ISO string, or a ms timestamp.  ``count``
   *  ignores this (it counts rows). */
  aggValue?: (row: T) => number | string | Date;
  /** Format the aggregated total for the footer / group cell (currency,
   *  units, a specific date format, …).  Receives the computed value
   *  (a number, or a ms timestamp for a ``'date'`` column) and the
   *  function used.  Defaults to a locale number string (2 decimals for
   *  ``avg``) or, for a date column, a date in the account timezone. */
  aggFormat?: (value: number, fn: AggFn) => React.ReactNode;

  // ── Pivot ────────────────────────────────────────────────────────
  /** Offer this column in the pivot panel's Rows / Columns pickers (a
   *  dimension to break the data down BY).  Measures come from
   *  ``aggregable`` columns instead — a column can be either or both. */
  pivotable?: boolean;
  /** Bucket a row falls into for this dimension.  This is how a
   *  timestamp column becomes a MONTH: return ``YYYY-MM`` computed in the
   *  ACCOUNT timezone (utils/datetime.ts) — a UTC bucket drops late-month
   *  loads into the wrong month.  Falls back to ``filterValue`` then the
   *  raw cell when omitted. */
  pivotValue?: (row: T) => string;
  /** Pretty-print a bucket for the header ('2026-01' -> 'Jan 2026').
   *  Receives the RAW bucket string from ``pivotValue``. */
  pivotLabel?: (bucket: string) => string;
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

// ── pillar-aware shape ─────────────────────────

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
  /** canonical subject identity.  ``driver_id``/``driver_name``
   *  are kept as aliases that always carry the same value. */
  subject_id?: string;
  subject_name?: string;
  subject_type?: 'driver' | 'vehicle';
  company: string;
  /** populated only when ``subject_type``
   *  is ``vehicle``.  Samsara-paired driver wins; ``assigned_driver_name``
   *  is the manual fallback from the per-account driver→truck map. */
  paired_driver_name?: string | null;
  assigned_driver_name?: string | null;
  /** last ~14 daily totals (oldest → newest)
   *  pulled from ``daily_scorecard_snapshots``.  Empty array when the
   *  subject has no snapshots yet (new tenants, or trucks added mid-window). */
  score_trend?: number[];
 // ── New canonical fields ─────────────────
  total?: number;                                 // 0-100, sum of pillar subtotals
  pillars?: Record<PillarKey, PillarSummary>;     // optional during one-release transition
  exposure?: {
    miles: number;
    drive_hours: number;
    idle_hours: number;
  };
  insufficient_data?: boolean;
  /** grace period — true when the driver has fewer than the
   *  probationary minimum of daily snapshots (currently 14).  These
   *  drivers stay visible but are excluded from rank pools so new
   *  hires can't outrank veterans on data they don't yet have. */
  probationary?: boolean;
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
  /** echoes back the ?subject= the request used. */
  subject?: 'driver' | 'vehicle';
  /** ISO-8601 UTC wall-clock at compute time. UI renders this as
   *  "Updated …" so operators can tell how stale the data is. */
  generated_at?: string;
}

export interface ScoreHistoryPoint {
  date: string;
  score: number;
  /**
 * present when the row was filtered to a specific
   * pillar (`?pillar=safety|efficiency|compliance`).  ``false`` means
   * the snapshot existed but the pillar didn't have enough exposure
   * data to produce a score.
   */
  has_data?: boolean;
}

export interface ScoreHistoryResponse {
  driver_id: string;
  /** echoed when the request used `?pillar=`. */
  pillar?: 'safety' | 'efficiency' | 'compliance' | null;
  history: ScoreHistoryPoint[];
  count: number;
}

/** Event entry inside a snapshot diff.  Mirrors ScoreEventBreakdown
 *  but the ``increased`` / ``decreased`` arrays additionally carry
 *  occurrence-count deltas so the UI can show "Speeding 2 → 5". */
export interface ScoreExplanationEvent extends ScoreEventBreakdown {
  occ_from?: number;
  occ_to?: number;
  occ_delta?: number;
}

/** Response shape for /api/safety/scorecards/{subject_id}/explanation
 *  — "Why did my score change?" diff between the latest snapshot and
 *  one ~N days ago.  No new schema; reads from
 *  ``daily_scorecard_snapshots.breakdown_json``. */
export interface ScoreExplanationResponse {
  subject_id: string;
  subject_type: 'driver' | 'vehicle';
  available: boolean;
  reason?: 'not_enough_snapshots' | 'breakdown_unparseable';
  snapshots_available?: number;
  from_date?: string;
  to_date?: string;
  from_score?: number;
  to_score?: number;
  score_delta?: number;
  penalties_added?:     ScoreExplanationEvent[];
  penalties_cleared?:   ScoreExplanationEvent[];
  penalties_increased?: ScoreExplanationEvent[];
  penalties_decreased?: ScoreExplanationEvent[];
  bonuses_earned?:      ScoreExplanationEvent[];
  bonuses_lost?:        ScoreExplanationEvent[];
  bonuses_increased?:   ScoreExplanationEvent[];
  bonuses_decreased?:   ScoreExplanationEvent[];
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
  aggregate_total_cost: number;
  aggregate_total_gallons: number;
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
  aggregate_avg_cpm: number;
  aggregate_avg_mpg: number;
  aggregate_total_miles: number;
  aggregate_total_cost: number;
}

// ── Admin ────────────────────────────────────────────────────

export interface AdminUser {
  id: number;
  /** ``null`` until the user opens the bot and links their Telegram. */
  telegram_id: number | null;
  display_name: string;
  role: string;
  /** Per-user manager tier on the base role (recruiter → recruiting team
   *  lead).  Set/cleared via PUT /admin/users/:id/manager. */
  is_manager: boolean;
  /** True when the role HAS a manager tier at all — drives whether the
   *  Team Management seniority toggle is shown for this row. */
  manager_capable: boolean;
  /** Senior-tier label for this role ("Manager" for recruiter, "Full admin"
   *  for admin), or null when the role has no tier.  Drives the toggle copy. */
  tier_senior_label?: string | null;
  /** True only for the primary (main) owner.  A role=owner row with this
   *  false is a co-owner.  Drives the Primary/Co-owner badge + which
   *  owner-management actions the primary owner sees. */
  is_primary_owner: boolean;
  truck_num: string | null;
  trucks: string[];
  allowed_companies: string[];
  is_active: boolean;
  email: string | null;
  language: string | null;
  timezone: string | null;
  /** Per-user Working Hours override (0-23, user's effective timezone).
   *  Defines the ACTIVE window during which alerts DELIVER to this
   *  user; outside that window non-critical alerts queue until
   *  shift-start.  When both are non-null the user has a personal
   *  override that wins over the role-level Working Hours.  Both
   *  ``null`` → inherits the role-level schedule.  Field names kept
   *  ``quiet_*`` to match the backend column names; the semantics are
   *  working-window, not silence-window.  Set/cleared via
   *  PUT /admin/users/:id/quiet-hours. */
  quiet_start: number | null;
  quiet_end:   number | null;
  /** FK to a work_hours.id from the catalog (migration 101).
   *  When set, the user uses THAT named schedule's window; replaces
   *  the legacy free-form quiet_start/end pair.  ``null`` →
   *  inherits the role-level Working Hours.  Edited via
   *  PUT /admin/users/:id/assigned-work-hours; the drawer dropdown
   *  reads this back to show which row is currently selected. */
  assigned_work_hours_id?: number | null;
  created_at: string | null;
}

export interface AdminUsersResponse {
  users: AdminUser[];
  count: number;
}

// ── Driver Module ───────────────────────────────────────────────

export interface DriverProfile {
  user_id: number;
  account_id: number;
  display_name: string;
  telegram_id: number | null;
  samsara_driver_id: string | null;
  cdl_number: string | null;
  cdl_state: string | null;
  cdl_class: string | null;
  cdl_expires: string | null;
  med_card_expires: string | null;
  hire_date: string | null;
  termination_date: string | null;
  dob: string | null;
  phone: string | null;
  home_address: string | null;
  driver_notes: string | null;
}

export interface DriverVehicleAssignment {
  id: number;
  account_id: number;
  user_id: number;
  vehicle_name: string;
  vehicle_id: string | null;
  is_primary: boolean;
  assigned_by: number | null;
  assigned_at: string;
  unassigned_at: string | null;
  notes: string | null;
  active: boolean;
}

export interface DriverDocument {
  id: number;
  account_id: number;
  user_id: number;
  doc_type: string;
  file_name: string;
  file_size: number | null;
  mime_type: string | null;
  issued_at: string | null;
  expires_at: string | null;
  status: string;
  uploaded_by: number | null;
  uploaded_at: string;
  notes: string | null;
  drive_file_id: string | null;
}

export interface DriverDetail {
  profile: DriverProfile;
  assignments: DriverVehicleAssignment[];
  documents: DriverDocument[];
}

export interface SamsaraDriver {
  samsara_driver_id: string;
  name: string;
  username: string;
  phone: string;
  company_code: string;
  deactivated: boolean;
  /** Local users.id this Samsara driver is already linked to, if any. */
  linked_user_id: number | null;
}

export interface SamsaraDriversResponse {
  drivers: SamsaraDriver[];
  count?: number;
  error?: string;
}

export interface InviteInfo {
  id: number;
  code: string;
  role: string;
  truck_num: string | null;
  expires_at: string;
  used_by: number | null;
  is_used: boolean;
  is_expired: boolean;
  /** Operator soft-deleted the invite via Team Management → Invites.
   *  Revoked rows are hidden from every redemption surface (bot /join,
   *  email-signup, mini-app) so the dashboard doesn't need to also
   *  hide them — but the StatusBadge MUST render a "Revoked" pill so
   *  an operator who has Show-all on doesn't see a green Pending pill
   *  next to a dead link.  Optional on the wire so a brief deploy-lag
   *  window (server has migration, response shape lags) doesn't blank
   *  out the panel — treat missing as ``false`` at every call-site. */
  is_revoked?: boolean;
  /** ISO-8601 UTC timestamp the operator pressed Revoke at, or null. */
  revoked_at?: string | null;
  /** Email-channel fields (migration 088 + admin.py email channel).
   *  All optional for deploy-lag tolerance — treat missing as link-
   *  channel.  ``channel`` is a derived classifier from the server
   *  (link | email); the raw fields below let the dashboard show
   *  who-was-emailed + last-send timestamp + resend attempt count. */
  channel?: 'link' | 'email';
  sent_to_email?: string | null;
  email_sent_at?: string | null;
  email_send_count?: number;
  /** Bounce / complaint state (migration 097).  Deploy-lag tolerant
   *  optionals — UIs that key off these treat missing as 'not bounced'.
   *  ``email_bounce_type`` distinguishes three operator-actionable states:
   *    'hard'      — undeliverable, dashboard shows Revoke & recreate
   *    'soft'      — transient, dashboard shows amber 'Delivery issues'
   *                  (count<3 keeps the soft state; >=3 promotes to
   *                  permanent bounce)
   *    'complaint' — recipient hit Report Spam; flagged but NOT
   *                  auto-revoked (silent destruction is worse than
   *                  an unactioned flag) */
  email_bounced_at?: string | null;
  email_bounce_type?: 'hard' | 'soft' | 'complaint' | null;
  email_bounce_reason?: string | null;
  email_soft_bounce_count?: number;
  email_complained_at?: string | null;
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
  /** Federal carrier ids — the stable key for matching synced records
   *  (e.g. Datatruck work orders) to this company. */
  mc_number?: string;
  usdot_number?: string;
  /** Brand/identity for the public application form + DQ packet. */
  brand_color?: string;
  website?: string;
  phone?: string;
  has_logo?: boolean;
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
  // ``schedules`` was removed from the canonical /admin/settings
  // response when Working Hours was consolidated into Team Management
  // → Working Hours tab.  Kept as an optional field on the type so the
  // Settings.tsx section that the linter restored can still compile
  // (it'll just render "No schedules configured" when the backend
  // omits the field — the tab is the canonical edit surface).
  schedules?: WorkSchedule[];
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
  /** Engine-hours threshold parallel to due_miles.  Required for trucks
   *  where idle wear dominates road wear (PTO, refrigerated, generator). */
  due_engine_hours: number | null;
  /** Last engine_hours reading from the warehouse scheduler. */
  last_engine_hours: number | null;
  status: string;
  /** 'low' | 'medium' | 'high' | 'critical'.  Default 'medium' on
   *  legacy rows so the badge always has something to render. */
  priority: string;
  recur_interval_days: number | null;
  recur_interval_miles: number | null;
  recur_interval_engine_hours: number | null;
  /** Set after the first overdue alert fires; gates the daily/6-h
   *  schedulers so a single crossing notifies exactly once. */
  alerted_at: string | null;
  /** Set after the pre-overdue ("due soon") notification fires.
   *  Distinct from alerted_at because the two notification windows are
   *  independent — a task can be warned today and still trigger the
   *  overdue alert 3 days later when it actually crosses. */
  warning_sent_at: string | null;
  /** Link to the Work Order row that closed this task (NULL while the
   *  task is open).  The Work Orders module owns CRUD for the linked
   *  row; the column lives here so we don't denormalize cost/vendor
   *  fields onto maintenance_tasks. */
  work_order_id: number | null;
  /** Telegram user id of whoever confirmed completion (bot driver or
   *  dashboard manager).  Raw id — display name resolved server-side
   *  into ``attested_by_name`` for the UI. */
  attested_by: number | null;
  attested_at: string | null;
  /** Server-resolved display name for ``attested_by``.  Absent when no
   *  attestation yet, OR when the platform user lookup failed. */
  attested_by_name?: string;
  /** Lineage: parent task id when this row was auto-spawned as a
   *  recurring follow-up or compliance auto-renewal.  Drives the
   *  "↻ Auto-renewed from #N" breadcrumb. */
  spawned_from_id: number | null;
  /** Server-computed projection for mileage-tracked tasks: when the
   *  task is expected to come due based on the vehicle's recent
   *  average daily miles.  ``null`` when the task has a hard
   *  ``due_date``, no telemetry exists for the truck, or the
   *  projection lands more than a year out.  Lets the calendar view
   *  place mileage tasks on a date grid with a visible "projected"
   *  marker. */
  projected_due_date?: string | null;
  /** Vehicle's median daily-miles velocity over the requested
   *  window (default 30 days, drive-days only) — surfaced alongside
   *  ``projected_due_date`` so the UI can disclose the rate the
   *  projection assumes ("projected from 187 mi/day, 22 drive days,
   *  last 30 days"). */
  velocity_avg_daily_miles?: number | null;
  /** Window the velocity was computed over (calendar days, before
   *  filtering down to drive-days).  Lets the tooltip say "last 30
   *  days" instead of hiding the window. */
  velocity_window_days?: number | null;
  /** Number of days within the window that actually had meaningful
   *  miles (the median's sample size).  Lets the tooltip disclose
   *  the sample's robustness — a low number ("3 drive days") signals
   *  the operator that the projection is on thin evidence. */
  velocity_drive_days?: number | null;
  /** Number of days within the window the vehicle reported any
   *  data (drive + idle).  Useful for distinguishing "yard truck
   *  with low utilization" (high days_observed, low drive_days)
   *  from "cold-start onboarding" (low days_observed). */
  velocity_days_observed?: number | null;
  /** Where the velocity came from: ``"daily_metrics"`` (preferred,
   *  730-day retention) or ``"snapshot_fallback"`` (used only when
   *  the daily aggregator hasn't populated the table yet).  Lets
   *  the operator see when a projection is on partial data. */
  velocity_source?: string | null;
  created_at: string;
  /** Set when status flips to 'completed' or 'done'; drives the service
   *  history timeline ordering. */
  completed_at: string | null;
  updated_at: string;
  /** ISO 8601 timestamp.  When set and in the future, the overdue and
   *  pre-overdue schedulers skip this task — the operator has acknowledged
   *  it ("parts on order", "shop visit booked") and snoozed the
   *  repeating red flag.  Cleared automatically when it falls past now. */
  snoozed_until?: string | null;
  /** Lightweight single attachment — the latest upload replaces the
   *  previous one.  Work Orders own the multi-file timeline for shop
   *  visits; this is for driver-side roadside proof (DEF receipt,
   *  in-house oil-change photo). */
  attachment_name?: string | null;
  attachment_content_type?: string | null;
  /** Spend captured on completion for tasks closed outside the formal
   *  Work Order flow.  Integer cents — UI converts to dollars on the
   *  way in/out so no float drift on aggregate totals. */
  cost_cents?: number | null;
  vendor_name?: string | null;
}

export interface MaintenanceTemplate {
  id: number;
  account_id: number;
  name: string;
  task_type: string;
  description: string;
  priority: string;
  /** Relative offsets — applied to "now" / current odometer / current
   *  engine-hours when the template instantiates a real task. */
  due_in_days: number | null;
  due_in_miles: number | null;
  due_in_hours: number | null;
  recur_interval_days: number | null;
  recur_interval_miles: number | null;
  recur_interval_engine_hours: number | null;
  created_at: string;
  updated_at: string;
}

export interface MaintenanceTasksResponse {
  tasks: MaintenanceTask[];
  count: number;
}

// ── PTI (Pre-Trip Inspection) ───────────────────────────────────────

export interface PTIInspectionItem {
  id: number;
  inspection_id: number;
  item_key: string;
  label: string;
  category: string;
  /** 'pending' | 'ok' | 'minor' | 'major' | 'oos' | 'na' */
  status: string;
  notes: string | null;
  requires_media: number;
  required: number;
  sort_order: number;
  completed_at: string | null;
  /** Snapshotted from the template at spawn. */
  item_type?: string;
  reference_image_url?: string | null;
}

export interface PTIInspectionMedia {
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
  /** ISO timestamp set when the driver baked annotations into this blob. */
  annotated_at?: string | null;
  /** Per-photo AI vision review (null = never checked). */
  ai_review_status?: string | null;   // 'completed' | 'error'
  ai_review_result?: string | null;    // JSON: {verdict, confidence, summary, model}
  ai_reviewed_at?: string | null;
  /** Hybrid-storage state: 'local' | 'syncing' | 'remote' | 'stuck'.
   *  Legacy rows default to 'remote'.  Drives the small storage badge
   *  on the gallery thumbnail (💾 / 🔄 / ☁ / ⚠). */
  storage_state?: string | null;
}

export interface PTIInspectionRow {
  id: number;
  account_id: number;
  user_id: number;
  vehicle_name: string;
  trailer_name: string | null;
  inspection_type: string;
  status: 'scheduled' | 'in_progress' | 'submitted' | 'reviewed' | 'revision_required';
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
  created_at: string;
  inspected_at: string;
  /** Base64 PNG data URL — present once captured. */
  driver_signature?: string | null;
  driver_signed_at?: string | null;
  reviewer_signature?: string | null;
  reviewer_signed_at?: string | null;
  /** Where the inspection happened (vehicle telematics or device GPS). */
  location_lat?: number | null;
  location_lon?: number | null;
  location_source?: string | null;   // 'vehicle' | 'device'
  location_at?: string | null;
}

export interface PTIInspectionDetail extends PTIInspectionRow {
  items: PTIInspectionItem[];
  media: PTIInspectionMedia[];
  /** Resolved name of the user who reviewed (server joins users). */
  reviewed_by_name?: string;
  /** Resolved display name of the driver who submitted. */
  driver_name?: string;
}

export interface PTIInspectionsResponse {
  items: PTIInspectionRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface PTITemplateItem {
  id: number;
  template_id: number;
  item_key: string;
  label: string;
  category: string;
  requires_media: number;
  required: number;
  sort_order: number;
  /** 'check' (status buttons) | 'photo' (guided photo) | 'document' (upload). */
  item_type?: string;
  /** ObjectStore filename of the reference example photo (null = none). */
  reference_image_url?: string | null;
}

export interface PTITemplate {
  id: number;
  account_id: number;
  vehicle_type: 'truck' | 'trailer';
  inspection_type: string;
  version: number;
  is_active: number;
  created_at: string;
  updated_at: string;
  items: PTITemplateItem[];
}

// ── Work Orders ──────────────────────────────────────────────

/** Shop-invoice record.  Links to many maintenance tasks via
 *  ``maintenance_tasks.work_order_id`` (one visit closes many tasks). */
export interface WorkOrder {
  id: number;
  account_id: number;
  company_code: string;
  vehicle_id: string;
  vehicle_name: string;
  /** Asset class for this work order: 'truck' | 'trailer' | '' (unset). */
  vehicle_type: string;
  vendor_name: string;
  vendor_address: string;
  vendor_phone: string;
  /** Registry-only contact captured on the form — never stored on the
   *  work-order row itself (email isn't invoice truth). */
  vendor_email?: string;
  service_date: string | null;
  odometer_at_service: number | null;
  engine_hours_at_service: number | null;
  labor_cost: number;
  parts_cost: number;
  tax_amount: number;
  /** Additional charge beyond itemized parts + labor (shop /
   *  environmental / call-out fee).  total = labor+parts+fee+tax. */
  fee_amount: number;
  total_cost: number;
  invoice_number: string;
  payment_method: string;
  /** unpaid / paid / partial / void ("void" shows as "Written off") */
  payment_status: string;
  /** open / in_progress / completed */
  status: string;
  /** Reason-for-repair class: scheduled / non_scheduled / emergency,
   *  or '' when unclassified.  Splits planned upkeep from unplanned
   *  firefighting for spend analysis. */
  repair_priority?: string;
  /** 3C repair documentation (DOT / warranty standard): what the
   *  driver reported / what the shop found / what they did. */
  complaint?: string;
  cause?: string;
  correction?: string;
  notes: string;
  /** Who the work order is assigned to (synced from Datatruck's
   *  assigned_to, or set by the operator). */
  assigned_to?: string;
  /** Vendor registry link (features/vendors).  The vendor_* snapshot
   *  fields stay the invoice truth; this id is the analytical spine. */
  vendor_id?: number | null;
  /** Provenance: 'manual' (hand-entered) or an integration id like
   *  'datatruck'.  Drives the Source column badge on the list. */
  source?: string;
  /** Upstream id for integration-sourced rows; '' for manual. */
  external_id?: string;
  /** Source system's human reference (Datatruck "WO-00983"); '' for
   *  manual or when the integration exposes none. */
  external_number?: string;
  created_by: number;
  created_at: string;
  updated_at: string;
}

/** Line item on a work order. */
export interface WorkOrderPart {
  id: number;
  work_order_id: number;
  part_name: string;
  part_number: string;
  quantity: number;
  unit_cost: number;
  total_cost: number;
  warranty_months: number;
  /** Task-type slug this line belongs to ('brakes', 'custom_…');
   *  '' = untagged.  Shares the maintenance task-type vocabulary. */
  service_task?: string;
  notes: string;
}

/** Attachment metadata — invoice PDF, shop photo, warranty doc.  File
 *  bytes are served via GET /work-orders/{id}/attachments/{aid}. */
export interface WorkOrderAttachment {
  id: number;
  work_order_id: number;
  /** Backend-specific locator (disk path / Drive file ID / S3 key).
   *  Frontend never parses this — it just calls the download route. */
  file_path: string;
  file_name: string;
  file_size: number;
  content_type: string;
  /** invoice / photo / warranty / receipt / other */
  kind: string;
  uploaded_by: number;
  /** Server-resolved display name for ``uploaded_by``. */
  uploaded_by_name?: string;
  uploaded_at: string;
}

export interface WorkOrdersResponse {
  work_orders: WorkOrder[];
  count: number;
}

export interface WorkOrderLaborLine {
  id: number;
  work_order_id?: number;
  service_task: string;
  description: string;
  hours: number;
  rate: number;
  total_cost: number;
}

export interface WorkOrderDetail {
  work_order: WorkOrder;
  parts: WorkOrderPart[];
  /** Itemized labor lines (Tier-2 B1); labor_cost is their sum when present. */
  labor?: WorkOrderLaborLine[];
  attachments: WorkOrderAttachment[];
  linked_tasks: MaintenanceTask[];
}

// ── Vendors (registry — features/vendors) ───────────────────

export interface Vendor {
  id: number;
  account_id: number;
  name: string;
  name_key: string;
  address: string;
  phone: string;
  email: string;
  notes: string;
  created_at: string;
  updated_at: string;
  /** Rollups present on the list endpoint. */
  work_order_count?: number;
  total_spent?: number;
  last_service_date?: string | null;
  /** Link to the platform global directory (Phase C). */
  global_vendor_id?: number | null;
}

/** Global-directory identity (active entries only; identity fields
 *  only — the platform never exposes account data through it). */
export interface DirectoryEntry {
  id: number;
  name: string;
  address: string;
  phone: string;
  email: string;
  website: string;
  services: string;
  /** Operator-confirmed coordinates — null until geocoded on the
   *  system console; entries without them never appear on map layers. */
  lat?: number | null;
  lng?: number | null;
  /** Multi-location brand family ("TA / Petro"); '' = independent. */
  chain?: string;
  /** Directory BROWSE only: the caller's own vendor linked to this
   *  entry (their one account-specific fact — nobody else's). */
  linked_vendor_id?: number | null;
  linked_vendor_name?: string | null;
  /** Approved-only community signal (anonymous). */
  rating_avg?: number | null;
  rating_count?: number;
  reviews?: Array<{ rating: number; comment: string; month: string }>;
  /** The caller account's own review (any status) — lets the UI show
   *  "your review is pending review". */
  my_review?: { rating: number; comment: string; status: string } | null;
}

/** Parts-catalog record (features/parts) with the list endpoint's
 *  usage rollups. */
export interface CatalogPart {
  id: number;
  account_id: number;
  name: string;
  name_key: string;
  part_number: string;
  notes: string;
  created_at: string;
  updated_at: string;
  usage_count?: number;
  total_spent?: number;
  /** Link to the platform public parts catalog (part_directory). */
  global_part_id?: number | null;
  /** Level 2 of System→Assembly→Part; '' = Unassigned. */
  assembly_key?: string;
  /** Server-side keyword guess for a blank assembly — suggest-confirm. */
  suggested_assembly?: string;
  /** Unlink honesty marker: TRUE after the user unlinks — the adopt
   *  fan-out never silently re-links a suppressed row. */
  public_link_suppressed?: boolean;
}

/** Public parts catalog identity (ACTIVE entries only; operator-
 *  curated on the platform — identity fields, never usage data). */
export interface PublicPartEntry {
  id: number;
  name: string;
  category: string;
  part_number: string;
  description: string;
  status?: string;
  /** Browse only: the caller's own part linked to this entry. */
  linked_part_id?: number | null;
  linked_part_name?: string | null;
  /** Browse only, market intel live + account sharing: the NATIONAL
   *  typical range (p25–p75, 3+ companies, 12 months). */
  est_p25?: number;
  est_p75?: number;
}

/** One published geographic price cell for a catalog part. */
export interface PartGeoCell {
  scope: 'national' | 'state';
  region: string;
  companies: number;
  invoices: number;
  p25: number;
  p75: number;
  window_months: number;
  computed_at: string;
}

/** The part drill-down payload (GET /parts/{id}) — void invoices and
 *  drafts never count, same rule as every cost report. */
export interface PartAnalytics {
  part: CatalogPart;
  by_vehicle: Array<{
    vehicle_name: string;
    usage_count: number;
    work_order_count: number;
    total_quantity: number;
    total_spent: number;
    first_date: string | null;
    last_date: string | null;
    visit_days: number;
    /** Mean gap between distinct service days; null under 2 visits. */
    avg_interval_days: number | null;
  }>;
  by_vendor: Array<{
    vendor_name: string;
    vendor_id: number | null;
    purchases: number;
    total_quantity: number;
    total_spent: number;
    avg_unit_price: number | null;
    min_unit_price: number | null;
    max_unit_price: number | null;
    last_date: string | null;
  }>;
  purchases: Array<{
    work_order_id: number;
    service_date: string;
    vehicle_name: string;
    vendor_name: string;
    quantity: number;
    unit_cost: number;
    total_cost: number;
    service_task: string;
    effective_unit_price: number | null;
  }>;
  /** Linked public-catalog identity (category displays through this
   *  join — never copied onto the user's row); null when unlinked. */
  public?: PublicPartEntry | null;
  /** Geographic market estimates — triple-gated server-side (platform
   *  switch, catalog link, give-to-get).  reason: disabled |
   *  not_linked | not_sharing (with available_count tease). */
  market?: {
    available: boolean;
    reason?: 'disabled' | 'not_linked' | 'not_sharing';
    available_count?: number;
    national?: PartGeoCell | null;
    states?: PartGeoCell[];
  };
}

/** Anonymized market rollup row (Phase D — published shape only). */
export interface MarketRollupRow {
  dim_type: 'service_task' | 'part';
  dim_key: string;
  dim_label: string;
  companies: number;
  invoices: number;
  p25: number;
  p75: number;
  window_months: number;
  computed_at: string;
}

export interface WorkOrderCostRow {
  vehicle_name?: string;
  task_type?: string;
  /** Service-task slug (per-service-task report); 'untagged' bucket
   *  keeps unclassified spend visible. */
  service_task?: string;
  /** Itemized labor merged per task (Tier-2 B1); total_spent stays parts. */
  labor_spent?: number;
  /** Part-level fields (per-part report). */
  part_name?: string;
  usage_count?: number;
  total_quantity?: number;
  vendor_name?: string;
  work_order_count: number;
  total_spent: number;
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
  /** Everything parked that this user may see, BEFORE the
   *  attention-only narrowing — so the list can say what it is hiding.
   *  Absent on /parking/history, which has no such filter. */
  total_active?: number;
}

export interface ParkingStatsResponse {
  total_parked: number;
  unsafe: number;
  unknown: number;
  safe: number;
}

// ── AI Assistant ──────────────────────────────────────────────────
//
// Type contracts now live with the AI feature in features/ai/types.ts.
// Re-exported here so existing imports `from '../../types'` keep
// working — new AI code should import directly from
// features/ai/types instead.

export type {
  AIUsage,
  AIChatMessage,
  AIChatResponse,
  AISummaryResponse,
  AIDiagnoseResponse,
  AITier,
  AITierChoice,
  AIModel,
  AIModelsResponse,
  AITierOption,
  AITierResponse,
  AITierSwitchResponse,
  AIHistoryResponse,
  AIConversation,
  AIConversationsResponse,
  AIConversationMessagesResponse,
  AIProcessStep,
} from '../features/ai/types';

// ── Scheduled Reports ────────────────────────────────────────────

export interface ScheduledReport {
  id?: number;
  user_id?: number;
  frequency: string;
  report_type: string;
  send_hour: number;
  timezone: string;
  /** Comma-separated list of delivery channels — "telegram", "email",
   *  or "telegram,email".  Legacy rows pre-2026-06 default to
   *  "telegram".  Email channel requires a verified user email; the
   *  API rejects updates that try to enable it without that. */
  delivery_channels?: string;
  is_active?: number;
  created_at?: string;
}
