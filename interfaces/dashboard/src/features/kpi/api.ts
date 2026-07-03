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

export async function getKpiThresholds(): Promise<ThresholdsResponse> {
  return apiJSON<ThresholdsResponse>('/kpi/thresholds');
}

export async function putKpiThresholds(
  thresholds: Record<string, number>,
): Promise<ThresholdsResponse> {
  return apiJSON<ThresholdsResponse>('/kpi/thresholds', {
    method: 'PUT',
    body: { thresholds },
  });
}
