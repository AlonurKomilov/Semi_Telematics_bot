/**
 * Shared applications data layer — ONE react-query entry used by both
 * the Applications page (table + board) and the topbar
 * ApplicationsHero, so the hero's pipeline counts always equal what
 * the page shows (same cache, no second fetch, no drift) — the same
 * contract as maintenance/useMaintenanceTasks.ts.
 *
 * Mutations on the page (drag-to-stage, bulk moves) update this cache
 * optimistically via qc.setQueryData / invalidateQueries, which
 * re-renders the hero in the same tick.
 */
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../api/client';

/** Pipeline stages in wire order.  'hired' is terminal-positive;
 *  'rejected' / 'withdrawn' are terminal-negative. */
export const APP_STATUSES = [
  'submitted', 'screening', 'interview', 'approved', 'rejected', 'withdrawn', 'hired',
] as const;

export interface AppRow {
  id: number; reference: string; status: string; first_name: string; last_name: string;
  email: string; phone: string; city: string; state: string; cdl_state: string;
  cdl_class: string; position_type: string; years_cdl: string; submitted_at: string;
  /** Another application in this account shares SSN/email/phone. */
  duplicate?: boolean;
}

/** The newest APP_PAGE_LIMIT applications, one fetch per session (the
 *  table filters client-side and the board groups by status — both need
 *  the set in hand).
 *
 *  `total` is the REAL row count from the server. Past the limit the
 *  loaded slice is not the pipeline, and every count derived from it —
 *  hero chips, segment badges, board columns — is a count of the slice.
 *  Callers must disclose that rather than present it as the whole. */
export const APP_PAGE_LIMIT = 500;

export function useApplicationsQuery() {
  return useQuery({
    queryKey: ['applications'],
    queryFn: () => apiJSON<{ items: AppRow[]; total?: number }>(
      `/applications?limit=${APP_PAGE_LIMIT}`,
    ),
    placeholderData: (prev) => prev,
  });
}

/** Per-status tallies for the hero chips. */
export function countAppsByStatus(rows: AppRow[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const r of rows) counts[r.status] = (counts[r.status] ?? 0) + 1;
  return counts;
}
