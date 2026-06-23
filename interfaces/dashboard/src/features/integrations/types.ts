/**
 * Shared type shapes for the Integrations page.
 *
 * Keep these aligned with the backend serializers in
 * ``interfaces/api/routes/integrations.py``:
 *
 *   * ``_serialize_catalog_entry`` → CatalogEntry
 *   * ``_serialize_integration``   → AccountIntegration
 *
 * The shapes are stable across providers; provider-specific
 * differences (auth_kind, credential shape) ride inside
 * ``feature_defaults`` and the credentials JSON.
 */

export type ProviderStatus =
  | 'available'
  | 'beta'
  | 'coming_soon'
  | 'deprecated';

export interface FeatureToggle {
  enabled?: boolean;
  interval_sec?: number;
  interval_min?: number;
  interval_hour?: number;
  cron?: string;
}

export type FeatureToggleMap = Record<string, FeatureToggle>;

/** Shape of data the provider exposes — drives which router file
 *  owns its routes and which card body the frontend renders.
 *  Backend source: `ProviderKind` in `adapters/telematics/catalog.py`. */
export type ProviderKind = 'telematics' | 'tms';

export interface CatalogEntry {
  provider_id: string;
  display_name: string;
  tagline: string;
  description: string;
  capabilities: string[];
  auth_kind: string;
  /** New 2026-06-11 — defaults to 'telematics' on entries the backend
   *  doesn't tag explicitly (back-compat with older catalog serializers). */
  kind: ProviderKind;
  docs_url: string;
  icon: string;
  status: ProviderStatus;
  feature_defaults: FeatureToggleMap;
  is_registered: boolean;
}

export interface AccountIntegration {
  account_id: number;
  provider_id: string;
  status: 'connected' | 'error' | 'disabled' | 'disconnected';
  connected_at: string;
  has_credentials: boolean;
  feature_toggles: FeatureToggleMap;
  cadence_overrides: FeatureToggleMap;
  last_health_at: string;
  last_health_error: string;
  /** ISO timestamp of the last successful M5 history backfill.
   *  Empty string when no backfill has ever completed (fresh connect,
   *  in-flight backfill, or unsupported provider).  The dashboard
   *  card uses this for a "Last backfill: …" line so operators can
   *  see when fresh data last landed without depending on the 24h
   *  Redis status badge. */
  last_backfill_at: string;
  created_by: number;
  created_at: string;
  updated_at: string;
}

export interface IntegrationsListResponse {
  catalog: CatalogEntry[];
  integrations: AccountIntegration[];
}

export interface TestConnectionResponse {
  ok: boolean;
  message: string;
  provider_account_id: string;
}

export interface BackfillStatus {
  state: 'idle' | 'queued' | 'running' | 'completed' | 'failed' | 'skipped';
  account_id?: number;
  provider_id?: string;
  days_requested?: number;
  days_done?: number;
  days_skipped_already_present?: number;
  days_total?: number;
  rows_inserted?: number;
  api_calls?: number;
  elapsed_sec?: number;
  started_at?: string;
  finished_at?: string;
  reason?: string;
  errors?: string[];
  triggered_by?: number;
}

export interface SnapshotCoverageDay {
  day_utc: string;
  row_count: number;
}

export interface SnapshotCoverageResponse {
  account_id: number;
  provider_id: string;
  provider_display_name: string;
  days: number;
  coverage: SnapshotCoverageDay[];
}

/** Per-company /me probe result persisted in Redis for 7 days.
 *  ``null`` means "untested" (never probed, or TTL expired) — the
 *  dashboard distinguishes this from ``{ok: false}`` (tested + failing). */
export interface CompanyHealth {
  ok: boolean;
  message: string;
  checked_at: number;        // epoch seconds
  elapsed_ms?: number;
}

/** Aggregate health derived from the per-company map. */
export interface ProviderHealthSummary {
  healthy: number;
  total: number;
  untested: number;
  status: 'unknown' | 'healthy' | 'degraded' | 'error';
}

/** One row per company on the account, with the credential-status
 *  flag the Integration card needs to render its "Connected companies"
 *  section.  Raw tokens are NEVER sent to the client — the boolean is
 *  the only signal of whether a key is set. */
export interface ProviderCompanyEntry {
  code: string;
  display_name: string;
  has_key: boolean;
  active_days: number;
  /** Latest probe result.  ``null`` when never tested. */
  health: CompanyHealth | null;
}

export interface ProviderCompaniesResponse {
  account_id: number;
  provider_id: string;
  health_summary: ProviderHealthSummary;
  companies: ProviderCompanyEntry[];
}

/** Per-company test endpoint response. */
export interface TestCompanyResponse {
  code: string;
  ok: boolean;
  message: string;
  elapsed_ms: number;
}

// ── Telematics "Synced data" feeds (Samsara) ────────────────────

/** One data feed Samsara pushes into the warehouse, with its stored
 *  freshness — the telematics counterpart to ``DatatruckResourceOverview``
 *  so both cards render the same "Synced data" table. */
export interface ProviderFeed {
  /** Capability id — the frontend derives label + cadence from it. */
  capability: string;
  /** "scheduled" | "scheduled+backfill" — drives which action shows. */
  mode: string;
  /** Product feature this feed serves — groups the Synced-data table. */
  feature: string;
  /** Sub-component within the feature (e.g. Vehicles → Telemetry / Health /
   *  Faults) — the card's second grouping level.  Empty → no sub-group. */
  component: string;
  enabled: boolean;
  stored: { count: number; last_synced_at: string | null };
}

export interface ProviderFeedsResponse {
  provider_id: string;
  feeds: ProviderFeed[];
}

// ── Datatruck TMS sync ──────────────────────────────────────────

/** Live state of one resource's sync run (Redis-backed, 24h TTL).
 *  ``null`` when no run has ever been recorded. */
export interface DatatruckSyncRun {
  state: 'queued' | 'running' | 'completed' | 'failed';
  resource: string;
  pages_done: number;
  records_written: number;
  /** Upstream total from the first page's count — lets the UI show
   *  "synced 500 of 17,093" when a page cap truncated the run. */
  total_upstream: number | null;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  /** Per-id detail enrichment outcome (work orders).  When
   *  ``detail_failed`` > 0 the integration token couldn't reach the
   *  detail endpoint, so rich fields (invoice, payment, vendor contact)
   *  fell back to blank — ``detail_error`` carries the reason. */
  detail_ok?: number;
  detail_failed?: number;
  detail_error?: string | null;
}

// ── Sync preview (plan → apply) ────────────────────────────────

export interface SyncDiffVehicles {
  kind: 'vehicles';
  vehicle_type: string;
  /** Will be inserted (no match). */
  new: { unit: string; vin: string; plate: string }[];
  /** Matched — will fill these empty fields, nothing overwritten. */
  enrich: { unit: string; matched_unit: string; by: string; fills: string[] }[];
  /** Would insert, but the unit already exists on >1 vehicle — review. */
  review: { unit: string; vin: string; plate: string; reason: string }[];
  counts: {
    new: number; enrich: number; review: number;
    unchanged: number; total: number;
  };
}

interface WorkOrderFieldChange {
  field: string;
  from: string;
  to: string;
}

interface WorkOrderDiffRow {
  external_id: string;
  number: string;
  vendor: string;
  vehicle: string;
  total: number | null;
  /** Per-field before → after, only on update rows. */
  changes?: WorkOrderFieldChange[];
}

export interface SyncDiffWorkOrders {
  kind: 'work_orders';
  new: WorkOrderDiffRow[];
  update: WorkOrderDiffRow[];
  counts: { new: number; update: number; changed?: number; total: number };
}

export type SyncDiff = SyncDiffVehicles | SyncDiffWorkOrders;

/** Poll payload for a background preview run.  ``diff`` is present once
 *  ``state === 'ready'``. */
export interface DatatruckPreviewStatus {
  state: 'queued' | 'pending' | 'running' | 'ready' | 'failed'
    | 'expired' | 'unavailable';
  resource: string;
  preview_id: string;
  fetched?: number;
  total_upstream?: number | null;
  diff?: SyncDiff;
  error?: string | null;
}

/** One resource row in the sync overview. */
export interface DatatruckResourceOverview {
  /** Whether the matching capability toggle is on. */
  enabled: boolean;
  capability: string;
  /** Product feature this resource lands in — groups the table. */
  feature?: string;
  stored: { count: number; last_synced_at: string | null };
  sync: DatatruckSyncRun | null;
}

export interface DatatruckSyncStatusResponse {
  account_id: number;
  provider_id: string;
  resources: Record<string, DatatruckResourceOverview>;
}
