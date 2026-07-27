/**
 * Row count per Status tab, from the server.
 *
 * The tabs render their count as a badge, and the board holds a capped
 * page of a larger queue — so a badge tallied from loaded rows would put
 * a confidently wrong number in the most prominent place on the page.
 *
 * Deliberately shares the board's other filters (type / severity / search
 * / window) but NOT its ack-state: switching tabs must change only the
 * one dimension, so each tab has to answer "how many, if I switched to
 * you, with everything else as it is".
 */
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../../api/client';
import { useAlertsFilters } from './useAlertsFilters';

interface SegmentCountsResponse {
  counts: Record<string, number>;
}

export function useAlertSegmentCounts(): {
  counts: Record<string, number> | undefined;
} {
  const { typeFilter, severityFilter, vehicleSearch, days } = useAlertsFilters();

  const q = useQuery<SegmentCountsResponse>({
    queryKey: ['alerts', 'segment-counts', typeFilter, severityFilter, vehicleSearch, days],
    queryFn: () => {
      const params = new URLSearchParams();
      if (typeFilter && typeFilter !== 'all') params.set('alert_type', typeFilter);
      if (severityFilter && severityFilter !== 'all') params.set('severity', severityFilter);
      if (vehicleSearch) params.set('q', vehicleSearch);
      params.set('days', String(days));
      return apiJSON<SegmentCountsResponse>(
        `/alerts/pending/segment-counts?${params.toString()}`,
      );
    },
    placeholderData: (prev) => prev,
  });

  return { counts: q.data?.counts };
}
