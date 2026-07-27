/**
 * Shared query for the Alerts page — single source of truth for the
 * /alerts/pending endpoint + the
 * queryKey shape.
 *
 * Every section that needs alert data calls this hook; TanStack
 * Query dedupes on the queryKey so only ONE network request fires
 * per filter combination regardless of how many sections subscribe.
 *
 * vehicle-grouped aggregate so the page count reflects vehicle cards
 * (``Page 1 of 2`` for 80 trucks) rather than alert rows (``Page 1
 * of 22`` for 2164 alerts) — same UX rule as the pre-refactor page.
 *
 * ``placeholderData: prev => prev`` keeps the table populated while a
 * refetch is in flight — without it, every filter change blanks the
 * page to a spinner.  Replicates the pre-refactor UX exactly.
 */
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../../api/client';
import type { AlertsResponse, VehiclesAlertsResponse } from '../../../types';
import { useAlertsFilters } from './useAlertsFilters';

// No fixed page size any more: the board asks for ONE page and the page
// size is the operator's choice, carried in the URL.
//
// The old constant was 2,000 — the server's cap — because filtering and
// sorting ran in the browser and would otherwise narrow a fragment while
// answering for the whole queue.  Both moved to the server, so the grid
// only ever needs the rows on screen.  A 3,984-row queue is 160 pages of
// 25, not "Batch 1 of 2".

export interface UseAlertsQueryResult {
  data: AlertsResponse | VehiclesAlertsResponse | undefined;
  isLoading: boolean;
  isFetching: boolean;
  error: unknown;
  refetch: () => void;
  dataUpdatedAt: number;
  pageSize: number;
}

export function useAlertsQuery(): UseAlertsQueryResult {
  const {
    typeFilter,
    severityFilter,
    ackState,
    vehicleSearch,
    page,
    pageSize,
    sort,
    dir,
    days,
  } = useAlertsFilters();

  const queryKey = [
    'alerts',
    typeFilter,
    severityFilter,
    vehicleSearch,
    ackState,
    page,
    pageSize,
    sort,
    dir,
    days,
  ] as const;

  const q = useQuery<AlertsResponse | VehiclesAlertsResponse>({
    queryKey,
    queryFn: () => {
      const params = new URLSearchParams();
      if (typeFilter !== 'all') params.set('alert_type', typeFilter);
      if (severityFilter !== 'all') params.set('severity', severityFilter);
      // ``q`` searches vehicle name OR location server-side.  The old
      // ``vehicle`` param matched names only, and location was searchable
      // solely within the rows already loaded — which quietly meant "some
      // of your alerts".  One box, one meaning, whole queue.
      if (vehicleSearch) params.set('q', vehicleSearch);
      params.set('ack_state', ackState);
      params.set('days', String(days));
      if (sort) { params.set('sort', sort); params.set('dir', dir); }
      params.set('page_size', String(pageSize));
      params.set('page', String(page));
      const qs = params.toString();
      return apiJSON<AlertsResponse | VehiclesAlertsResponse>(
        `/alerts/pending${qs ? `?${qs}` : ''}`,
      );
    },
    placeholderData: (prev) => prev,
  });

  return {
    data: q.data,
    isLoading: q.isLoading,
    isFetching: q.isFetching,
    error: q.error,
    refetch: q.refetch,
    dataUpdatedAt: q.dataUpdatedAt,
    pageSize,
  };
}
