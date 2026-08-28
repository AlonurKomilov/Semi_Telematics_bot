/**
 * Thin API client wrappers for the Integrations page.
 *
 * Every route here goes through ``apiJSON`` so retry / auth / error
 * shape is consistent with the rest of the dashboard.  No React-
 * Query usage here — the hooks layer wraps these for caching.
 */

import { apiJSON } from '../../api/client';
import type {
  AccountIntegration,
  BackfillStatus,
  DatatruckSyncStatusResponse,
  IntegrationsListResponse,
  DatatruckPreviewStatus,
  ProviderCompaniesResponse,
  ProviderFeedsResponse,
  SnapshotCoverageResponse,
  TestCompanyResponse,
  TestConnectionResponse,
} from './types';

const BASE = '/integrations';

export async function listIntegrations(): Promise<IntegrationsListResponse> {
  return apiJSON<IntegrationsListResponse>(BASE);
}

// ── Vehicle source precedence (when Samsara + Datatruck disagree) ──
// Lives under /vehicles (owner policy on vehicle data) but is surfaced on
// the Integrations page, so the client wrapper lives here.

export interface PrecedenceField {
  key: string;
  label: string;
  primary: string;
}

export interface LifecycleSource {
  key: string;
  /** Only verbs whose MECHANISM exists — datatruck has no inactivate
   *  path, so it never carries the flag and no dead switch renders. */
  verbs: Record<string, boolean>;
}

export interface SourcePrecedence {
  sources: string[];
  fields: PrecedenceField[];
  lifecycle?: { sources: LifecycleSource[] };
}

export async function getIntegrationsConfig(): Promise<SourcePrecedence> {
  return apiJSON<SourcePrecedence>('/vehicles/config');
}

export async function putIntegrationsConfig(
  primary: Record<string, string>,
  lifecycle?: Record<string, Record<string, boolean>>,
): Promise<SourcePrecedence> {
  return apiJSON<SourcePrecedence>('/vehicles/config', {
    method: 'PUT',
    // `lifecycle` omitted = untouched server-side, so the
    // precedence-only save this panel has always sent stays identical.
    body: lifecycle ? { primary, lifecycle } : { primary },
  });
}

// ── One-time Datatruck driver import (onboarding) ────────────────

export interface DriverImportEntry {
  external_id: string;
  name: string;
  phone: string;
  email: string;
  status: string;
  reason?: string;
  matched_name?: string;
  by?: string;
}

export interface DriverImportPlan {
  kind: string;
  already_linked: number;
  link: DriverImportEntry[];
  create: DriverImportEntry[];
  review: DriverImportEntry[];
  skipped: DriverImportEntry[];
  counts: Record<string, number>;
}

export interface DriverImportResult {
  created: number;
  linked: number;
  review: number;
  skipped: number;
}

export async function getDriverImportPlan(): Promise<DriverImportPlan> {
  return apiJSON<DriverImportPlan>(`${BASE}/datatruck/drivers/import-plan`);
}

export async function applyDriverImport(): Promise<DriverImportResult> {
  return apiJSON<DriverImportResult>(`${BASE}/datatruck/drivers/import`, {
    method: 'POST',
  });
}

// ── Cross-source data conflicts ──────────────────────────────────

export interface DataConflict {
  id: number;
  entity_type: string;
  entity_id: number;
  field: string;
  current_value: string;
  current_source: string;
  incoming_value: string;
  incoming_source: string;
  detected_at: string;
}

export async function getConflicts(): Promise<{ conflicts: DataConflict[] }> {
  return apiJSON<{ conflicts: DataConflict[] }>(`${BASE}/conflicts`);
}

export async function resolveConflict(
  conflictId: number,
  chosenValue: string,
): Promise<{ resolved: boolean; conflicts: DataConflict[] }> {
  return apiJSON(`${BASE}/conflicts/${conflictId}/resolve`, {
    method: 'POST',
    body: { chosen_value: chosenValue },
  });
}

export async function connectIntegration(
  providerId: string,
  credentials: Record<string, unknown>,
): Promise<AccountIntegration> {
  return apiJSON<AccountIntegration>(
    `${BASE}/${encodeURIComponent(providerId)}/connect`,
    {
      method: 'POST',
      // Pass the dict directly — ``apiFetch`` adds the
      // ``Content-Type: application/json`` header and stringifies
      // when it sees an object body.  Pre-stringifying here would
      // bypass that path and the request would arrive without the
      // header, which FastAPI rejects with a 422 "valid dictionary"
      // error because Pydantic can't bind the raw bytes.
      body: { credentials },
    },
  );
}

export async function disconnectIntegration(
  providerId: string,
): Promise<{ state: string; provider_id: string }> {
  return apiJSON<{ state: string; provider_id: string }>(
    `${BASE}/${encodeURIComponent(providerId)}`,
    { method: 'DELETE' },
  );
}

export async function updateToggles(
  providerId: string,
  featureToggles: Record<string, Record<string, unknown>>,
  cadenceOverrides?: Record<string, Record<string, unknown>>,
): Promise<AccountIntegration> {
  // Defensive: drop any entries whose value isn't an object.  Legacy
  // rows can carry null / boolean values for some capabilities and
  // the dashboard's spread-and-update pattern preserves them; the
  // backend rejects non-object values from this surface.  Normalising
  // here keeps the wire payload clean even when the source state is
  // mid-migration.
  const cleanToggles: Record<string, Record<string, unknown>> = {};
  for (const [cap, value] of Object.entries(featureToggles || {})) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      cleanToggles[cap] = value;
    } else if (typeof value === 'boolean') {
      cleanToggles[cap] = { enabled: value };
    }
    // null / undefined / primitives that aren't booleans → drop the
    // entry so the backend falls back to its catalog default.
  }
  return apiJSON<AccountIntegration>(
    `${BASE}/${encodeURIComponent(providerId)}/toggles`,
    {
      method: 'PUT',
      // See ``connectIntegration`` for why we pass an object, not a
      // pre-stringified payload — the apiFetch wrapper sets the
      // Content-Type header only when it sees an unstringified body.
      body: {
        feature_toggles: cleanToggles,
        cadence_overrides: cadenceOverrides ?? null,
      },
    },
  );
}

export async function testConnection(
  providerId: string,
): Promise<TestConnectionResponse> {
  return apiJSON<TestConnectionResponse>(
    `${BASE}/${encodeURIComponent(providerId)}/actions/test-connection`,
    { method: 'POST' },
  );
}

export async function triggerBackfill(
  providerId: string,
  days: number,
): Promise<{ state: string; days: number }> {
  return apiJSON<{ state: string; days: number }>(
    `${BASE}/${encodeURIComponent(providerId)}/actions/backfill-history`,
    {
      method: 'POST',
      // See ``connectIntegration`` for why we pass an object, not a
      // pre-stringified payload.
      body: { days },
    },
  );
}

export async function getBackfillStatus(
  providerId: string,
): Promise<BackfillStatus> {
  return apiJSON<BackfillStatus>(
    `${BASE}/${encodeURIComponent(providerId)}/actions/backfill-history/status`,
  );
}

/** Per-feed freshness for the telematics "Synced data" table (Samsara). */
export async function getProviderFeeds(
  providerId: string,
): Promise<ProviderFeedsResponse> {
  return apiJSON<ProviderFeedsResponse>(
    `${BASE}/${encodeURIComponent(providerId)}/feeds`,
  );
}

export async function getSnapshotCoverage(
  providerId: string,
  days = 30,
): Promise<SnapshotCoverageResponse> {
  return apiJSON<SnapshotCoverageResponse>(
    `${BASE}/${encodeURIComponent(providerId)}/snapshot-coverage?days=${days}`,
  );
}

// ── Per-company key management (canonical) ─────────────────────

export async function listProviderCompanies(
  providerId: string,
): Promise<ProviderCompaniesResponse> {
  return apiJSON<ProviderCompaniesResponse>(
    `${BASE}/${encodeURIComponent(providerId)}/companies`,
  );
}

export async function setCompanyCredential(
  providerId: string,
  companyCode: string,
  apiToken: string,
): Promise<AccountIntegration> {
  return apiJSON<AccountIntegration>(
    `${BASE}/${encodeURIComponent(providerId)}/companies/${encodeURIComponent(companyCode)}/credentials`,
    { method: 'PUT', body: { api_token: apiToken } },
  );
}

export async function removeCompanyCredential(
  providerId: string,
  companyCode: string,
): Promise<AccountIntegration> {
  return apiJSON<AccountIntegration>(
    `${BASE}/${encodeURIComponent(providerId)}/companies/${encodeURIComponent(companyCode)}/credentials`,
    { method: 'DELETE' },
  );
}

/** Run the lean /me probe against ONE company.  Returns inline
 *  result + persists in Redis for 7 days so the next
 *  ``listProviderCompanies`` call reflects the new ``health``. */
export async function testCompanyConnection(
  providerId: string,
  companyCode: string,
): Promise<TestCompanyResponse> {
  return apiJSON<TestCompanyResponse>(
    `${BASE}/${encodeURIComponent(providerId)}/companies/${encodeURIComponent(companyCode)}/actions/test`,
    { method: 'POST' },
  );
}

/** Queue a per-company history backfill.  Same shape as
 *  ``triggerBackfill`` but scoped to one company so the operator
 *  can refresh just one company's data after a key rotation. */
export async function triggerCompanyBackfill(
  providerId: string,
  companyCode: string,
  days: number,
): Promise<{ state: string; days: number; company_code: string }> {
  return apiJSON<{ state: string; days: number; company_code: string }>(
    `${BASE}/${encodeURIComponent(providerId)}/companies/${encodeURIComponent(companyCode)}/actions/backfill-history`,
    { method: 'POST', body: { days } },
  );
}

/** Read the per-company backfill status from Redis. */
export async function getCompanyBackfillStatus(
  providerId: string,
  companyCode: string,
): Promise<BackfillStatus> {
  return apiJSON<BackfillStatus>(
    `${BASE}/${encodeURIComponent(providerId)}/companies/${encodeURIComponent(companyCode)}/actions/backfill-history/status`,
  );
}

/** Clear a stuck backfill record from Redis.  The dashboard exposes
 *  this as a "Reset" button next to the BackfillStatusBadge so the
 *  operator can recover from a stale ``running`` state immediately
 *  rather than waiting for the 5-min heartbeat staleness coercion. */
export async function resetBackfillStatus(
  providerId: string,
): Promise<{ cleared: boolean }> {
  return apiJSON<{ cleared: boolean }>(
    `${BASE}/${encodeURIComponent(providerId)}/actions/backfill-history/reset`,
    { method: 'POST' },
  );
}

// ── Datatruck TMS sync ──────────────────────────────────────────

/** Queue one resource's sync (drivers / trucks / trailers / orders /
 *  work_orders).  Fire-and-forget — poll ``getDatatruckSyncStatus``
 *  to watch progress.  409s when a run is already in flight or the
 *  resource's toggle is disabled.
 *
 *  ``days`` is an optional history window (1–365) applied server-side
 *  to date-ranged resources (orders, work_orders); roster resources
 *  ignore it.  Omit it for the provider default. */
export async function triggerDatatruckSync(
  resource: string,
  days?: number,
): Promise<{ state: string; resource: string }> {
  const qs = days ? `?days=${days}` : '';
  return apiJSON<{ state: string; resource: string }>(
    `${BASE}/datatruck/sync/${encodeURIComponent(resource)}${qs}`,
    { method: 'POST' },
  );
}

/** One call returns every resource's toggle state, stored count,
 *  last-synced stamp, and live run state — backs the whole "Synced
 *  data" panel without a per-resource fan-out. */
export async function getDatatruckSyncStatus(): Promise<DatatruckSyncStatusResponse> {
  return apiJSON<DatatruckSyncStatusResponse>(
    `${BASE}/datatruck/sync-status`,
  );
}

/** Kick off a background dry-run for a previewable resource.  Returns a
 *  ``preview_id`` immediately; poll ``getDatatruckPreview`` until the
 *  state is ``ready`` (then ``applyDatatruckSync`` commits it). */
export async function startDatatruckPreview(
  resource: string,
  days?: number,
): Promise<{ state: string; resource: string; preview_id: string }> {
  const qs = days ? `?days=${days}` : '';
  return apiJSON<{ state: string; resource: string; preview_id: string }>(
    `${BASE}/datatruck/sync/${encodeURIComponent(resource)}/preview${qs}`,
    { method: 'POST' },
  );
}

/** Poll a running preview for its state + diff. */
export async function getDatatruckPreview(
  resource: string,
  previewId: string,
): Promise<DatatruckPreviewStatus> {
  return apiJSON<DatatruckPreviewStatus>(
    `${BASE}/datatruck/sync/${encodeURIComponent(resource)}/preview/${encodeURIComponent(previewId)}`,
  );
}

/** Commit a previewed resource from its cached rows.  Fire-and-forget —
 *  poll ``getDatatruckSyncStatus`` for progress. */
export async function applyDatatruckSync(
  resource: string,
  previewId: string,
): Promise<{ state: string; resource: string }> {
  return apiJSON<{ state: string; resource: string }>(
    `${BASE}/datatruck/sync/${encodeURIComponent(resource)}/apply`,
    { method: 'POST', body: { preview_id: previewId } },
  );
}
