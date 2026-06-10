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

export interface CatalogEntry {
  provider_id: string;
  display_name: string;
  tagline: string;
  description: string;
  capabilities: string[];
  auth_kind: string;
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
