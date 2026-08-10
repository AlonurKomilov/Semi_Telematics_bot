/**
 * KPI API client + shapes (backend: features/kpi/router.py).
 */

import { apiJSON } from '../../api/client';

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

export async function putIncentiveTargets(
  targets: Record<number, number>,
): Promise<{ targets: CompanyTarget[] }> {
  return apiJSON<{ targets: CompanyTarget[] }>(
    '/kpi/config/incentives/targets',
    { method: 'PUT', body: { targets } },
  );
}
