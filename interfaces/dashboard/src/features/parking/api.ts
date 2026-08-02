/**
 * Parking API surface — every call this feature makes, in one place.
 *
 * The row/response TYPES stay in ``src/types`` rather than moving here:
 * ``features/live-map/sections/UnsafeParkingMarkers.tsx`` consumes
 * ``ParkingEventsResponse`` too, and a cross-feature import of another
 * feature's api.ts is exactly the coupling the feature-centric layout
 * exists to prevent.  Shapes used ONLY by parking (the unified events
 * response, vehicle history) are declared here.
 */
import { apiJSON, apiFetch } from '../../api/client';
import type { ParkingEvent, ParkingEventsResponse } from '../../types';

export type { ParkingEvent, ParkingEventsResponse };

/** Build a query string, omitting empty params.
 *
 * `undefined` is dropped rather than serialised, so a caller can express
 * "leave this to the server default" by passing it — which the callers
 * below rely on (`attention_only` is only sent to OVERRIDE the default).
 * `URLSearchParams` would otherwise stringify it to the literal
 * `"undefined"` and the server would parse that as a value.
 *
 * Returns `''` (not `'?'`) when nothing survives, so the path stays clean.
 */
function qs(params: Record<string, string | number | boolean | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

/** A row as the grid consumes it: the stored event plus the two derived
 *  fields the page adds — ``flagged_count`` from the server,
 *  ``ai_confidence`` parsed at the page boundary. */
export interface ParkingRow extends ParkingEvent {
  /** Flagged (unsafe + unverified) parking events for this vehicle,
   *  server-computed over the retention window. */
  flagged_count?: number;
  /** HIGH / MEDIUM / LOW, parsed out of ai_analysis. */
  ai_confidence?: string;
}

/** One vehicle's parking events — the detail panel's history section. */
export async function listVehicleParkingHistory(
  vehicleId: string, limit = 50,
): Promise<{ events: ParkingEvent[]; count: number }> {
  return apiJSON<{ events: ParkingEvent[]; count: number }>(
    `/parking/vehicle/${encodeURIComponent(vehicleId)}/history${qs({ limit })}`,
  );
}

/** Unresolved only.  Kept for the current page until phase 3 replaces it
 *  with the unified read; live-map has its own call. */
export async function listActiveParking(
  opts: { vehicle?: string; attentionOnly?: boolean } = {},
): Promise<ParkingEventsResponse> {
  return apiJSON<ParkingEventsResponse>(`/parking/active${qs({
    vehicle: opts.vehicle,
    // The endpoint defaults to true; only send the override.
    attention_only: opts.attentionOnly === false ? 'false' : undefined,
  })}`);
}

export async function resolveParkingEvent(eventId: number): Promise<void> {
  await apiJSON(`/parking/${eventId}/resolve`, { method: 'POST' });
}

/** The AI location snapshot.  Returns the raw Response so the caller can
 *  distinguish 404 (no image for this event) from a transport failure —
 *  a distinction that was previously collapsed to `null` and rendered as
 *  a permanent "Loading…". */
export async function fetchParkingMapImage(eventId: number): Promise<Response> {
  return apiFetch(`/parking/${eventId}/map-image`);
}
