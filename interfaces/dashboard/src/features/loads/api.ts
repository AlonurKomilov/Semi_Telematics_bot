/**
 * Loads API client + shapes (backend: features/loads/router.py).
 *
 * Types live here (not types/index.ts) — the feature owns its shapes;
 * nothing outside the feature consumes them yet.
 */

import { apiJSON } from '../../api/client';

export const LOAD_STATUSES = [
  'upcoming', 'dispatched', 'in_transit', 'delivered', 'canceled',
] as const;
export type LoadStatus = (typeof LOAD_STATUSES)[number];

export interface LoadRow {
  id: number;
  load_number: string;
  status: string;
  payment_status: string;
  customer: string;
  company_code: string;
  pickup_location: string;
  pickup_date: string;
  delivery_location: string;
  delivery_date: string;
  driver_user_id: number | null;
  driver_name: string;
  dispatcher_user_id: number | null;
  dispatcher_name: string;
  vehicle_unit: string;
  trailer_unit: string;
  total_rate: number | null;
  loaded_miles: number | null;
  empty_miles: number | null;
  driver_pay: number | null;
  other_costs: number | null;
  /** Derived server-side at read time. */
  total_miles: number | null;
  rpm: number | null;
  gross: number | null;
  source: string;
  notes: string;
}

export interface LoadsResponse {
  loads: LoadRow[];
  counts: Record<string, number>;
}

export type LoadDraft = Partial<
  Omit<LoadRow, 'id' | 'total_miles' | 'rpm' | 'gross' | 'source'>
>;

export async function listLoads(status?: string): Promise<LoadsResponse> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : '';
  return apiJSON<LoadsResponse>(`/loads/${qs}`);
}

export async function createLoad(draft: LoadDraft): Promise<LoadRow> {
  return apiJSON<LoadRow>('/loads/', { method: 'POST', body: draft });
}

export async function updateLoad(
  id: number, draft: LoadDraft,
): Promise<LoadRow> {
  return apiJSON<LoadRow>(`/loads/${id}`, { method: 'PUT', body: draft });
}

export async function deleteLoad(id: number): Promise<{ deleted: boolean }> {
  return apiJSON<{ deleted: boolean }>(`/loads/${id}`, { method: 'DELETE' });
}
