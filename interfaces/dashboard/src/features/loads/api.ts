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
  /** Per-account sequential Load ID (1, 2, 3…). */
  seq: number | null;
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
  /** Extra pay & costs (line items) aggregated per bucket. */
  extra_driver_pay: number | null;
  extra_costs: number | null;
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
  // True when the account has more rows than the server returns per
  // request — the UI must say so rather than silently cutting off.
  truncated?: boolean;
}

export type LoadDraft = Partial<
  Omit<LoadRow,
    'id' | 'total_miles' | 'rpm' | 'gross' | 'source'
    | 'extra_driver_pay' | 'extra_costs'>
>;

// ── Line items — extra pay & costs (TONU / layover / tolls / …) ──

export const LINE_ITEM_KINDS = [
  'tonu', 'layover', 'bonus', 'detention', 'lumper', 'tolls', 'other',
] as const;
export type LineItemKind = (typeof LINE_ITEM_KINDS)[number];

export interface LineItem {
  id: number;
  load_id: number | null;
  driver_user_id: number | null;
  dispatcher_user_id: number | null;
  kind: string;
  /** Which side of gross it hits. */
  bucket: 'driver_pay' | 'expense';
  amount: number;
  item_date: string;
  notes: string;
}

export interface LineItemDraft {
  kind: string;
  amount: number;
  bucket?: 'driver_pay' | 'expense';
  load_id?: number;
  driver_user_id?: number;
  dispatcher_user_id?: number;
  item_date?: string;
  notes?: string;
}

export async function listLineItems(loadId: number): Promise<LineItem[]> {
  const r = await apiJSON<{ items: LineItem[] }>(`/loads/${loadId}/line-items`);
  return r.items;
}

export async function createLineItem(draft: LineItemDraft): Promise<{ id: number }> {
  return apiJSON<{ id: number }>('/loads/line-items', {
    method: 'POST', body: { ...draft },
  });
}

export async function deleteLineItem(itemId: number): Promise<void> {
  await apiJSON(`/loads/line-items/${itemId}`, { method: 'DELETE' });
}

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
