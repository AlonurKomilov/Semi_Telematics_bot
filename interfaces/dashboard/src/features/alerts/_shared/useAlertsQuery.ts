/**
 * Shared query for the Alerts page — single source of truth for the
 * /alerts/pending (or /alerts/pending/by-vehicle) endpoint + the
 * queryKey shape.
 *
 * Every section that needs alert data calls this hook; TanStack
 * Query dedupes on the queryKey so only ONE network request fires
 * per filter combination regardless of how many sections subscribe.
 *
 * The endpoint chosen depends on viewMode: ``by-vehicle`` hits the
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

// Full-window fetch: the results section is a DataGrid that filters /
// sorts / groups / paginates CLIENT-side, so it needs the whole filter
// window loaded — page-scoped slices would make those operations lie
// (a "Severity: critical" filter that silently misses criticals on
// page 2).  2000 matches the server cap; beyond it AlertsResults shows
// a truncation notice and the server pager steps through overflow.
const PAGE_SIZE = 2000;

export interface UseAlertsQueryResult {
  data: AlertsResponse | VehiclesAlertsResponse | undefined;
  isLoading: boolean;
  isFetching: boolean;
  error: unknown;
  refetch: () => void;
  dataUpdatedAt: number;
  /** True when the active viewMode pulls from /alerts/pending/by-vehicle. */
  useVehicleEndpoint: boolean;
  pageSize: number;
}

export function useAlertsQuery(): UseAlertsQueryResult {
  const {
    viewMode,
    typeFilter,
    severityFilter,
    ackState,
    vehicleSearch,
    page,
    days,
  } = useAlertsFilters();

  // Both view modes read the same FLAT endpoint now — the per-vehicle
  // presentation is DataGrid row-grouping over the same rows, done
  // client-side in AlertsResults.  One endpoint, one cache entry, and
  // toggling the view doesn't refetch (viewMode is out of the key).
  void viewMode;
  const pageSize = PAGE_SIZE;

  const queryKey = [
    'alerts',
    typeFilter,
    severityFilter,
    vehicleSearch,
    ackState,
    page,
    days,
  ] as const;

  const q = useQuery<AlertsResponse | VehiclesAlertsResponse>({
    queryKey,
    queryFn: () => {
      const params = new URLSearchParams();
      if (typeFilter !== 'all') params.set('alert_type', typeFilter);
      if (severityFilter !== 'all') params.set('severity', severityFilter);
      if (vehicleSearch) params.set('vehicle', vehicleSearch);
      params.set('ack_state', ackState);
      params.set('days', String(days));
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
    useVehicleEndpoint: false,
    pageSize,
  };
}
