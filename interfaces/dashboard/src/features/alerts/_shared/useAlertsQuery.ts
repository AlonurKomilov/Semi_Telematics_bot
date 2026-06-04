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

const PAGE_SIZE_LIST = 100;
const PAGE_SIZE_VEHICLES = 100;

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

  const useVehicleEndpoint = viewMode === 'by-vehicle';
  const pageSize = useVehicleEndpoint ? PAGE_SIZE_VEHICLES : PAGE_SIZE_LIST;

  const queryKey = [
    'alerts',
    viewMode,
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
      const path = useVehicleEndpoint
        ? '/alerts/pending/by-vehicle'
        : '/alerts/pending';
      const qs = params.toString();
      return apiJSON<AlertsResponse | VehiclesAlertsResponse>(
        `${path}${qs ? `?${qs}` : ''}`,
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
    useVehicleEndpoint,
    pageSize,
  };
}
