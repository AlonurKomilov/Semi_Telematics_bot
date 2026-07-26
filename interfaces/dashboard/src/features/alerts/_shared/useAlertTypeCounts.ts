/**
 * Per-type OPEN alert counts for the board's hero counters.
 *
 * Separate from ``useAlertsQuery`` on purpose.  That hook fetches the
 * board's current filter window, page-capped; a counter built from it
 * moves when you filter and stops at the cap.  These are totals over the
 * whole open queue, counted server-side, so they stay true regardless of
 * what the board below is showing.
 *
 * Keys are present only for alert types the ACTIVE VIEW may see — a
 * missing key means "not yours", which is not the same as 0.  Callers
 * must render nothing for an absent key rather than defaulting to zero.
 */
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../../api/client';

interface ByTypeResponse {
  counts: Record<string, number>;
}

export interface UseAlertTypeCountsResult {
  counts: Record<string, number> | undefined;
  isLoading: boolean;
}

export function useAlertTypeCounts(): UseAlertTypeCountsResult {
  // No filter values in the key: the response doesn't depend on them, so
  // one cache entry serves every filter combination on the page.
  const q = useQuery<ByTypeResponse>({
    queryKey: ['alerts', 'by-type'],
    queryFn: () => apiJSON<ByTypeResponse>('/alerts/pending/by-type'),
    placeholderData: (prev) => prev,
  });

  return { counts: q.data?.counts, isLoading: q.isLoading };
}
