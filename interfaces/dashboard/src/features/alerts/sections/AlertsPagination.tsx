/**
 * Server-batch navigation — shown ONLY when the chosen window holds more
 * alerts than one fetch returns.
 *
 * It is deliberately NOT a second table paginator: DataGrid's own footer
 * pages the rows already loaded, while this steps to the next BATCH from
 * the server.  Two "Showing X–Y of Z" lines over different denominators
 * was the confusion this replaced, so the wording says "Batch" and states
 * the window it counts within.
 */
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useAlertsFilters } from '../_shared/useAlertsFilters';
import { useAlertsQuery } from '../_shared/useAlertsQuery';

export default function AlertsPagination() {
  const { page, setPage, days, ackState } = useAlertsFilters();
  const { data, isFetching, pageSize } = useAlertsQuery();

  // Render ONLY when the window overflows a single fetch.  Otherwise
  // DataGrid's own footer is already the one true paginator for this
  // table, and a second "Showing X–Y of Z" beside it (over a different
  // denominator) reads as two competing pagers with three totals.
  if (!data || data.count <= 0) return null;
  if ((data.total_pages ?? 1) <= 1) return null;

  const total = data.count;
  const ps = data.page_size ?? pageSize;
  const cur = data.page ?? page;
  const start = total === 0 ? 0 : (cur - 1) * ps + 1;
  const end = Math.min(cur * ps, total);

  return (
    <div className="flex items-center justify-between mt-3">
      <p className="text-xs text-muted-foreground">
        Batch <strong>{start.toLocaleString()}</strong>–
        <strong>{end.toLocaleString()}</strong> of{' '}
        <strong>{total.toLocaleString()}</strong>{' '}
        {/* The open queue is unwindowed, so naming a window here would be
            a claim the query doesn't make. */}
        {ackState === 'active'
          ? 'open alerts'
          : `alerts in the last ${days} days`}
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
            Batch <strong>{data.page ?? page}</strong> of{' '}
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
