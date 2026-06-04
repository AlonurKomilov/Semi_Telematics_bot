import { useMemo } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { FileText, Plus, Paperclip, Receipt, X } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
import {
  PageHeader, EmptyState, ErrorState, TableSkeleton,
} from '../../components/shell';
import type { WorkOrder, WorkOrdersResponse, AnyColumn } from '../../types';
import { toneClasses, type Tone } from '../../lib/status';

// Status / payment → tone.  Matches the maintenance module's
// StatusBadge styling family so the two modules read as siblings.
// These vocabularies (submitted / void / partial) aren't in the
// shared ``statusTone`` map, so the tone is pinned here and the soft
// pill is rendered through the shared ``toneClasses`` recipe.
const STATUS_TONE: Record<string, Tone> = {
  draft:     'neutral',
  submitted: 'info',
  paid:      'ok',
  void:      'danger',
};

const PAYMENT_TONE: Record<string, Tone> = {
  unpaid:  'warn',
  paid:    'ok',
  partial: 'warn',
  void:    'neutral',
};

function Pill({ value, palette }: { value: unknown; palette: Record<string, Tone> }) {
  const v = String(value || '').toLowerCase();
  const cls = toneClasses(palette[v] ?? 'neutral');
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium capitalize ${cls}`}>
      {v || '—'}
    </span>
  );
}

function MoneyCell({ value }: { value: unknown }) {
  const n = Number(value ?? 0);
  // Render with thousands separators + 2 decimals so $1,234.50 is read
  // at a glance.  Zero shows muted so empty totals don't shout.
  return (
    <span className={`tabular-nums font-medium ${n === 0 ? 'text-muted-foreground' : ''}`}>
      ${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
    </span>
  );
}

const columns: AnyColumn[] = [
  { key: 'id', label: '#', sortable: true, render: (v) => <span className="font-mono text-xs text-muted-foreground">{`#${v}`}</span> },
  { key: 'vehicle_name', label: 'Vehicle', sortable: true },
  { key: 'vendor_name', label: 'Vendor', sortable: true, render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  {
    key: 'service_date', label: 'Service Date', sortable: true,
    render: (v) => v ? new Date(String(v)).toLocaleDateString() : <span className="text-muted-foreground">—</span>,
  },
  { key: 'total_cost', label: 'Total', sortable: true, render: (v) => <MoneyCell value={v} /> },
  { key: 'status', label: 'Status', sortable: true, render: (v) => <Pill value={v} palette={STATUS_TONE} /> },
  { key: 'payment_status', label: 'Payment', sortable: true, render: (v) => <Pill value={v} palette={PAYMENT_TONE} /> },
  // Invoice number is the operator's primary cross-reference to their
  // own bookkeeping system — surface it as a distinct column.
  { key: 'invoice_number', label: 'Invoice #', render: (v) => (v ? <span className="font-mono text-xs">{String(v)}</span> : <span className="text-muted-foreground">—</span>) },
];

export default function WorkOrders() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // URL params drive optional filters when the user click-throughs
  // from a Cost Reports chart.  ``?vehicle=221`` filters to one
  // truck; ``?vendor=Bob's`` to one vendor; ``?task_type=oil`` to
  // one type.  Empty params = show everything (default behaviour).
  // Vendor + task_type are filtered client-side because the list
  // endpoint only supports the ``vehicle`` filter today.
  const [params, setParams] = useSearchParams();
  const vehicleFilter = params.get('vehicle') || '';
  const vendorFilter = params.get('vendor') || '';
  const taskTypeFilter = params.get('task_type') || '';
  const hasFilter = !!(vehicleFilter || vendorFilter || taskTypeFilter);

  const { data, isLoading, error: queryError } = useQuery<WorkOrdersResponse>({
    // Include vehicle in the key so the server-side filter actually
    // takes effect via re-fetch; client-side filters (vendor / type)
    // narrow the cached result so they don't need a key bump.
    queryKey: ['work-orders', vehicleFilter],
    queryFn: () => {
      const qs = vehicleFilter
        ? `?vehicle=${encodeURIComponent(vehicleFilter)}`
        : '';
      return apiJSON<WorkOrdersResponse>('/work-orders' + qs);
    },
    placeholderData: (prev) => prev,
  });

  const rawWorkOrders = data?.work_orders ?? [];
  const workOrders = useMemo(() => {
    if (!vendorFilter && !taskTypeFilter) return rawWorkOrders;
    return rawWorkOrders.filter(w => {
      if (vendorFilter && !(w.vendor_name || '').toLowerCase().includes(vendorFilter.toLowerCase())) return false;
      // task_type filtering requires joining through linked
      // maintenance tasks — not surfaced on the work-order row
      // directly today.  When the field is absent we fall back to
      // showing all rows (the click-through doesn't have the join
      // server-side either).  Future revision could pull
      // /work-orders/{id} per row to enforce — not worth it for v1.
      return true;
    });
  }, [rawWorkOrders, vendorFilter, taskTypeFilter]);
  const clearFilters = () => {
    params.delete('vehicle'); params.delete('vendor'); params.delete('task_type');
    setParams(params, { replace: true });
  };
  const fetchError = queryError instanceof Error ? queryError.message : '';

  // Bucket counts for the filter chips — derived so they always match
  // the rendered set even if the upstream query refetches.  The "All"
  // bucket is just the full set.
  const buckets = useMemo(() => {
    const draft = workOrders.filter(w => w.status === 'draft');
    const submitted = workOrders.filter(w => w.status === 'submitted');
    const paid = workOrders.filter(w => w.status === 'paid');
    const unpaid = workOrders.filter(w => w.payment_status === 'unpaid' && w.status !== 'void');
    return { draft, submitted, paid, unpaid };
  }, [workOrders]);

  // Total spent across visible rows — useful management at-a-glance.
  const totalSpent = useMemo(
    () => workOrders.reduce((acc, w) => acc + (w.total_cost ?? 0), 0),
    [workOrders],
  );

  if (fetchError && workOrders.length === 0) {
    return (
      <div>
        <PageHeader icon={FileText} title={t('work_orders_page.list_title')} description={t('work_orders_page.list_description')} />
        <ErrorState message={fetchError} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        icon={FileText}
        title={t('work_orders_page.list_title')}
        description={t('work_orders_page.list_description')}
        actions={
          <Link
            to="/work-orders/new"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary/90 rounded-md text-xs font-medium text-primary-foreground transition"
          >
            <Plus size={14} />
            {t('work_orders_page.new_button')}
          </Link>
        }
      />

      {/* Active filter chips — appear above the summary when the
          user click-throughed from a Cost Reports chart so they
          immediately see how the list is narrowed and can clear. */}
      {hasFilter && (
        <div className="flex flex-wrap items-center gap-2 mb-4 text-xs">
          <span className="text-muted-foreground">{t('work_orders_page.filtered_to')}</span>
          {vehicleFilter && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-primary/10 border border-primary/30 rounded text-primary">
              {t('work_orders_page.filter_vehicle')}: <span>{vehicleFilter}</span>
            </span>
          )}
          {vendorFilter && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-primary/10 border border-primary/30 rounded text-primary">
              {t('work_orders_page.filter_vendor')}: {vendorFilter}
            </span>
          )}
          {taskTypeFilter && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-primary/10 border border-primary/30 rounded text-primary">
              {t('work_orders_page.filter_task_type')}: {taskTypeFilter}
            </span>
          )}
          <button
            type="button"
            onClick={clearFilters}
            className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
          >
            <X size={12} />
            {t('work_orders_page.clear_filters')}
          </button>
        </div>
      )}

      {/* Summary strip — total count + total spend.  Same visual
          weight as the maintenance summary cards so the two modules
          feel related. */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <div className="bg-card border border-border rounded-lg p-3">
          <p className="text-xs text-muted-foreground">{t('work_orders_page.card_count')}</p>
          <p className="text-xl font-bold tabular-nums">{workOrders.length}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-3">
          <p className="text-xs text-muted-foreground">{t('work_orders_page.card_total_spent')}</p>
          <p className="text-xl font-bold tabular-nums">
            ${totalSpent.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </p>
        </div>
        <div className="bg-card border border-border rounded-lg p-3">
          <p className="text-xs text-muted-foreground">{t('work_orders_page.card_draft')}</p>
          <p className="text-xl font-bold tabular-nums">{buckets.draft.length}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-3">
          <p className="text-xs text-muted-foreground">{t('work_orders_page.card_unpaid')}</p>
          <p className="text-xl font-bold tabular-nums">{buckets.unpaid.length}</p>
        </div>
      </div>

      {isLoading && workOrders.length === 0 ? (
        <TableSkeleton rows={6} cols={8} />
      ) : workOrders.length === 0 ? (
        <EmptyState
          icon={Receipt}
          title={t('work_orders_page.empty_title')}
          description={t('work_orders_page.empty_desc')}
          action={
            <Link
              to="/work-orders/new"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition"
            >
              <Plus size={14} />
              {t('work_orders_page.new_button')}
            </Link>
          }
        />
      ) : (
        <DataTable
          columns={columns}
          data={workOrders as unknown as Record<string, unknown>[]}
          searchKey="vendor_name"
          searchPlaceholder={t('work_orders_page.search_placeholder')}
          onRowClick={(row) => {
            const w = row as unknown as WorkOrder;
            navigate(`/work-orders/${w.id}`);
          }}
        />
      )}

      {/* Footer hint — explains the Paperclip icon convention used in
          the form / detail pages so users learn the vocabulary early. */}
      {workOrders.length > 0 && (
        <p className="text-xs text-muted-foreground mt-2 inline-flex items-center gap-1">
          <Paperclip size={12} />
          {t('work_orders_page.row_click_hint')}
        </p>
      )}
    </div>
  );
}
