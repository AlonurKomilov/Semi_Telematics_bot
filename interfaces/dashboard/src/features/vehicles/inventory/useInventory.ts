/**
 * Inventory data layer — queries + mutations for the Vehicle Inventory
 * card.  One query key family so every mutation invalidates the card
 * and the fleet-list badge together.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiJSON } from '../../../api/client';

export interface InventoryItem {
  id: number;
  vehicle_id: number;
  category: string;
  label: string;
  identifier: string;
  status: string;
  notes: string;
  installed_at: string;
  last_verified_at: string | null;
  last_verified_by: number | null;
}

export interface InventoryResponse {
  vehicle_id: number;
  items: InventoryItem[];
  summary: { total: number; attention: number };
  categories: string[];
  statuses: string[];
}

export interface InventoryEvent {
  id: number;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  from_vehicle_id: number | null;
  to_vehicle_id: number | null;
  actor_user_id: number | null;
  driver_user_id: number | null;
  actor_name: string;
  driver_name: string;
  note: string;
  created_at: string;
}

const qs = (company?: string) =>
  company ? `?company=${encodeURIComponent(company)}` : '';

export function useInventory(vehicleName: string, company?: string) {
  return useQuery<InventoryResponse>({
    queryKey: ['vehicle-inventory', vehicleName, company ?? ''],
    queryFn: () =>
      apiJSON<InventoryResponse>(
        `/vehicles/${encodeURIComponent(vehicleName)}/inventory${qs(company)}`,
      ),
    staleTime: 30_000,
  });
}

export function useInventoryEvents(itemId: number | null) {
  return useQuery<{ events: InventoryEvent[] }>({
    queryKey: ['vehicle-inventory-events', itemId],
    queryFn: () => apiJSON(`/vehicles/inventory/${itemId}/events`),
    enabled: itemId != null,
    staleTime: 15_000,
  });
}

/** Fleet-list badge: vehicle registry id → {total, attention}. */
export function useInventoryAlerts(enabled: boolean) {
  return useQuery<{ by_vehicle: Record<string, { total: number; attention: number }> }>({
    queryKey: ['vehicle-inventory-alerts'],
    queryFn: () => apiJSON('/vehicles/inventory/alerts'),
    enabled,
    staleTime: 60_000,
  });
}

/** All write paths invalidate the card + the fleet badge together. */
export function useInventoryMutations(vehicleName: string, company?: string) {
  const qc = useQueryClient();
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['vehicle-inventory', vehicleName, company ?? ''] });
    void qc.invalidateQueries({ queryKey: ['vehicle-inventory-alerts'] });
    void qc.invalidateQueries({ queryKey: ['vehicle-inventory-events'] });
  };

  const add = useMutation({
    mutationFn: (body: { category: string; label: string; identifier?: string; notes?: string }) =>
      apiJSON(`/vehicles/${encodeURIComponent(vehicleName)}/inventory`, {
        method: 'POST', body: { ...body, company: company || null },
      }),
    onSuccess: invalidate,
  });

  const patch = useMutation({
    mutationFn: ({ itemId, ...body }: {
      itemId: number; label?: string; identifier?: string; notes?: string;
      category?: string; status?: string; note?: string;
    }) => apiJSON(`/vehicles/inventory/${itemId}`, { method: 'PATCH', body }),
    onSuccess: invalidate,
  });

  const verify = useMutation({
    mutationFn: (itemId: number) =>
      apiJSON(`/vehicles/inventory/${itemId}/verify`, { method: 'POST', body: {} }),
    onSuccess: invalidate,
  });

  const transfer = useMutation({
    mutationFn: ({ itemId, toVehicleName, note }: { itemId: number; toVehicleName: string; note?: string }) =>
      apiJSON(`/vehicles/inventory/${itemId}/transfer`, {
        method: 'POST',
        body: { to_vehicle_name: toVehicleName, note: note ?? '', company: company || null },
      }),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: ({ itemId, note }: { itemId: number; note?: string }) =>
      apiJSON(`/vehicles/inventory/${itemId}/remove`, {
        method: 'POST', body: { note: note ?? '' },
      }),
    onSuccess: invalidate,
  });

  return { add, patch, verify, transfer, remove };
}
