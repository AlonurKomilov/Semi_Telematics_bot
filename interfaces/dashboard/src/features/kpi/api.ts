/**
 * KPI API client + shapes (backend: features/kpi/router.py).
 */

import { apiFetch, apiJSON } from '../../api/client';

export interface DispatcherKpi {
  dispatcher_user_id: number | null;
  dispatcher_name: string;
  revenue: number;
  loaded_miles: number;
  empty_miles: number;
  total_miles: number | null;
  empty_pct: number | null;
  rpm: number | null;
  driver_pay: number;
  other_costs: number;
  gross: number | null;
  loads: number;
  trucks: number;
  drivers: number;
  revenue_per_truck: number | null;
  gross_per_truck: number | null;
  grade: string;
}

export interface DispatcherKpisResponse {
  days: number;
  thresholds: Record<string, number>;
  dispatchers: DispatcherKpi[];
}

export async function getDispatcherKpis(days: number): Promise<DispatcherKpisResponse> {
  return apiJSON<DispatcherKpisResponse>(`/kpi/dispatchers?days=${days}`);
}

export interface ThresholdsResponse {
  thresholds: Record<string, number>;
  defaults?: Record<string, number>;
}

export async function getKpiConfig(): Promise<ThresholdsResponse> {
  return apiJSON<ThresholdsResponse>('/kpi/config');
}

export async function putKpiConfig(
  thresholds: Record<string, number>,
): Promise<ThresholdsResponse> {
  return apiJSON<ThresholdsResponse>('/kpi/config', {
    method: 'PUT',
    body: { thresholds },
  });
}

// ── Incentive configuration (/kpi/config/incentives) ─────────────────

export interface IncentiveTier {
  // ladder
  requires_target?: boolean;
  min_weekly_gross?: number | null;
  min_rpm?: number | null;
  // hybrid
  gross_min?: number; gross_max?: number;
  rpm_min?: number; rpm_max?: number;
  // fixed
  axis?: 'rpm' | 'gross';
  min?: number;
  // all
  pct: number;
}

export interface IncentiveConfig {
  model: 'ladder' | 'fixed' | 'hybrid';
  combine_rule: 'lower' | 'higher' | 'add';
  calc_cadence: 'weekly' | 'monthly' | 'custom';
  calc_custom_days: number | null;
  exception_cap_pct: number | null;
  floor_weekly_gross: number | null;
  floor_rpm: number | null;
  tiers: IncentiveTier[];
}

export interface CompanyTarget {
  company_id: number;
  company_code: string;
  company_name: string;
  weekly_gross_target: number;
}

export interface IncentivesResponse {
  config: IncentiveConfig | null;
  targets: CompanyTarget[];
  companies: { id: number; code: string; name: string }[];
  /** company code → last-4-weeks anchors for the targets editor. */
  company_stats: Record<string, { trucks: number; avg_truck_week: number }>;
}

export async function getIncentivesConfig(): Promise<IncentivesResponse> {
  return apiJSON<IncentivesResponse>('/kpi/config/incentives');
}

export async function putIncentivesConfig(
  body: IncentiveConfig,
): Promise<IncentivesResponse> {
  // Spread into a fresh literal: apiJSON's body wants an index-signature
  // record, which a named interface deliberately lacks.
  return apiJSON<IncentivesResponse>('/kpi/config/incentives', {
    method: 'PUT', body: { ...body },
  });
}

export interface RulesPreview {
  available: boolean;
  period_start?: string;
  period_end?: string;
  status?: string;
  rows?: number;
  current_total?: number;
  candidate_total?: number;
  delta?: number;
  moved_rows?: number;
}

/** Dry-run a candidate rule set against the latest run — nothing is
 *  written; 422s carry the engine's own message. */
export async function previewIncentiveRules(
  body: IncentiveConfig,
): Promise<RulesPreview> {
  return apiJSON<RulesPreview>('/kpi/config/incentives/preview', {
    method: 'POST', body: { ...body },
  });
}

export async function putIncentiveTargets(
  targets: Record<number, number>,
): Promise<{ targets: CompanyTarget[] }> {
  return apiJSON<{ targets: CompanyTarget[] }>(
    '/kpi/config/incentives/targets',
    { method: 'PUT', body: { targets } },
  );
}

// ── Incentive runs (/kpi/dispatch/runs) ──────────────────────────────

export interface RunSummary {
  id: number;
  period_start: string;
  period_end: string;
  status: 'draft' | 'finalized';
  created_at: string;
  finalized_at: string;
  /** Owner's one-line annotation; writable on any status (not money). */
  note?: string;
  /** Aggregates for the runs archive (one aggregate join server-side). */
  total: number;
  row_count: number;
}

export interface InactiveDate {
  date: string;
  reason: string;
}

export interface RunLoad {
  load_number: string;
  status: string;
  pickup_date: string;
  delivery_date: string;
  pickup_location: string;
  delivery_location: string;
  total_rate: number;
  miles: number;
}

export interface DaySuggestion {
  date: string;
  reason: string;
  /** Where the suggestion came from, e.g. "WO #123 · Vendor". */
  source: string;
}

export interface RunLoadsResponse {
  /** row id → its constituent loads (live data, may drift). */
  rows: Record<string, RunLoad[]>;
  /** row ids whose live loads no longer sum to the row's snapshot. */
  drift: number[];
  unmatched_loads: number;
  /** row id → maintenance-suggested inactive days (human confirms). */
  suggestions: Record<string, DaySuggestion[]>;
  /** dispatcher name → A–D grade for THIS period (analytics only). */
  dispatcher_grades: Record<string, string>;
}

export interface RunRow {
  id: number;
  dispatcher_user_id: number | null;
  dispatcher_name: string;
  company_code: string;
  vehicle_unit: string;
  window_start: string;
  window_end: string;
  total_days: number;
  inactive_days: number;
  inactive_reason: string;
  /** The board's per-day marks; the count above derives from it. */
  inactive_dates: InactiveDate[];
  base_gross: number;
  extras: number;
  extras_note: string;
  miles: number;
  weekly_target: number | null;
  kpi_gross: number;
  rpm: number | null;
  adjusted_target: number;
  pct: number;
  kpi_dollars: number;
  override_pct: number | null;
  override_reason: string;
  zero_reason: '' | 'no_target' | 'floor' | 'no_tier' | 'no_active_days';
  confirmed_dollars: number;
  /** Who last edited this row's inputs, and when (draft governance). */
  adjusted_by: number | null;
  adjusted_at: string;
  confirmed_by: number | null;
  confirmed_at: string;
  /** DRAFT only, ladder only: the nearest higher tier and the extra
   *  gross that reaches it — "$1,050 short of the 1.5% tier". */
  next_tier?: { pct: number; gap: number; dollars_at: number } | null;
  /** Paid rows only: the snapshot rule that produced this percent —
   *  the paid-row mirror of zero_reason.  Omitted for overrides,
   *  zeros, and rows whose stored pct an engine change can no longer
   *  reproduce (server-side drift guard). */
  matched_rule?:
    | { model: 'ladder'; pct: number; min_weekly_gross: number | null;
        min_rpm: number | null; requires_target: boolean }
    | { model: 'hybrid'; pct: number; gross_min: number; gross_max: number;
        rpm_min: number; rpm_max: number }
    | { model: 'fixed'; pct: number; combine_rule: 'lower' | 'higher' | 'add';
        rpm_pct: number; gross_pct: number };
}

export interface RunDetail extends RunSummary {
  rows: RunRow[];
  payouts: Record<string, number>;
  /** user id → display name, for the attribution fields. */
  user_names: Record<string, string>;
  /** The snapshot's policy knobs — lets the UI explain zeros with the
   *  thresholds that caused them (tiers stay server-side). */
  snapshot_config: {
    floor_weekly_gross: number | null;
    floor_rpm: number | null;
    exception_cap_pct: number | null;
  };
}

export async function listIncentiveRuns(): Promise<{ runs: RunSummary[] }> {
  return apiJSON<{ runs: RunSummary[] }>('/kpi/dispatch/runs');
}

export async function createIncentiveRun(
  period_start: string, period_end: string,
): Promise<RunDetail> {
  return apiJSON<RunDetail>('/kpi/dispatch/runs', {
    method: 'POST', body: { period_start, period_end },
  });
}

export async function getIncentiveRun(runId: number): Promise<RunDetail> {
  return apiJSON<RunDetail>(`/kpi/dispatch/runs/${runId}`);
}

export async function patchIncentiveRow(
  runId: number, rowId: number,
  patch: Partial<Pick<RunRow, 'window_start' | 'window_end'
    | 'inactive_days' | 'inactive_reason' | 'inactive_dates'
    | 'extras' | 'extras_note'>>,
): Promise<RunRow> {
  return apiJSON<RunRow>(
    `/kpi/dispatch/runs/${runId}/rows/${rowId}`,
    { method: 'PATCH', body: { ...patch } },
  );
}

export async function getIncentiveRunLoads(runId: number): Promise<RunLoadsResponse> {
  return apiJSON<RunLoadsResponse>(`/kpi/dispatch/runs/${runId}/loads`);
}

/** Download the settlement CSV — the sheet payroll receives (the house
 *  blob-download pattern; the server names the file by period+status). */
export async function downloadIncentiveRunCsv(runId: number): Promise<void> {
  const res = await apiFetch(`/kpi/dispatch/runs/${runId}/export.csv`);
  if (!res.ok) throw new Error('Export failed');
  const disposition = res.headers.get('content-disposition') ?? '';
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? 'incentive-run.csv';
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export interface MonthlyPayouts {
  month: string;
  runs: { id: number; period_start: string; period_end: string }[];
  payouts: Record<string, number>;
  total: number;
}

export async function getMonthlyPayouts(month: string): Promise<MonthlyPayouts> {
  return apiJSON<MonthlyPayouts>(`/kpi/dispatch/payouts?month=${month}`);
}

export interface MyPayoutRun {
  run_id: number;
  period_start: string;
  period_end: string;
  rows: Pick<RunRow, 'company_code' | 'vehicle_unit' | 'window_start'
    | 'window_end' | 'total_days' | 'inactive_days' | 'kpi_gross' | 'miles'
    | 'rpm' | 'pct' | 'confirmed_dollars' | 'zero_reason'>[];
  total: number;
}

export async function getMyPayouts(): Promise<{ runs: MyPayoutRun[] }> {
  return apiJSON<{ runs: MyPayoutRun[] }>('/kpi/dispatch/me');
}

export async function setIncentiveRunNote(runId: number, note: string): Promise<void> {
  await apiJSON(`/kpi/dispatch/runs/${runId}/note`, {
    method: 'PATCH', body: { note },
  });
}

export interface RunPreview {
  loads: number;
  trucks: number;
  dispatchers: number;
  gross: number;
}

export async function previewIncentiveRun(
  period_start: string, period_end: string,
): Promise<RunPreview> {
  return apiJSON<RunPreview>(
    `/kpi/dispatch/runs/preview?period_start=${period_start}&period_end=${period_end}`);
}

export async function setIncentiveException(
  runId: number, rowId: number,
  override_pct: number | null, reason: string,
): Promise<RunRow> {
  return apiJSON<RunRow>(
    `/kpi/dispatch/runs/${runId}/rows/${rowId}/exception`,
    { method: 'POST', body: { override_pct, reason } },
  );
}

export async function finalizeIncentiveRun(runId: number): Promise<RunDetail> {
  return apiJSON<RunDetail>(
    `/kpi/dispatch/runs/${runId}/finalize`, { method: 'POST' },
  );
}

export async function deleteIncentiveRun(runId: number): Promise<void> {
  await apiJSON<void>(`/kpi/dispatch/runs/${runId}`, { method: 'DELETE' });
}
