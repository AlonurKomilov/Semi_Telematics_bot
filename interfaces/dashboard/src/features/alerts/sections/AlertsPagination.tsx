/**
 * Pagination footer — Next/Prev step through whichever unit the
 * user is paginating (vehicles in per-vehicle mode, alerts in
 * per-alert mode).  The label flips so "Page 1 of 2 of 80 vehicles"
 * doesn't get confused with "Page 1 of 22 of 2164 alerts".
 *
 * Renders nothing when there's no data — silent in error / loading /
 * empty states (AlertsResults handles those branches).
 */
import { ChevronLeft, ChevronRight } from 'lucide-react';
import type { VehiclesAlertsResponse } from '../../../types';
import { useAlertsFilters } from '../_shared/useAlertsFilters';
import { useAlertsQuery } from '../_shared/useAlertsQuery';

export default function AlertsPagination() {
  const { page, setPage } = useAlertsFilters();
  const { data, isFetching, pageSize } = useAlertsQuery();

  // Discriminate the response shape — same logic as AlertsResults so
  // the "alerts" vs "vehicles" unit label matches what's rendered.
  const vehiclesData =
    data && Array.isArray((data as VehiclesAlertsResponse).vehicles)
      ? (data as VehiclesAlertsResponse)
      : undefined;

  if (!data || data.count <= 0) return null;

  const total = data.count;
  const ps = data.page_size ?? pageSize;
  const cur = data.page ?? page;
  const start = total === 0 ? 0 : (cur - 1) * ps + 1;
  const end = Math.min(cur * ps, total);
  const unit = vehiclesData ? 'vehicles' : 'alerts';

  return (
    <div className="flex items-center justify-between mt-3">
      <p className="text-xs text-muted-foreground">
        Showing <strong>{start}</strong>–<strong>{end}</strong> of{' '}
        <strong>{total}</strong> {unit}
      </p>
      {(data.total_pages ?? 1) > 1 && (
        <div className="flex items-center gap-2">
          <button
            onClick={() => setPage(Math.max(1, page - 1))}
            disabled={(data.page ?? page) <= 1 || isFetching}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-border text-xs font-medium text-foreground/80 hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed transition"
          >
            <ChevronLeft size={14} />
            Prev
          </button>
          <span className="text-xs text-muted-foreground tabular-nums">
            Page <strong>{data.page ?? page}</strong> of{' '}
            <strong>{data.total_pages ?? 1}</strong>
          </span>
          <button
            onClick={() => setPage(Math.min(data.total_pages ?? page, page + 1))}
            disabled={
              (data.page ?? page) >= (data.total_pages ?? 1) || isFetching
            }
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-border text-xs font-medium text-foreground/80 hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed transition"
          >
            Next
            <ChevronRight size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
