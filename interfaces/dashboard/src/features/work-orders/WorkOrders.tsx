import { useMemo } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { FileText, Plus, Paperclip, Receipt, X } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataGrid, { type DataGridSegment } from '../../components/DataGrid';
import {
  PageHeader, EmptyState, ErrorState, TableSkeleton,
} from '../../components/shell';
import type { WorkOrder, WorkOrdersResponse, AnyColumn } from '../../types';

// Payment lifecycle tabs (B3 phase 1).  Evidence from live data: every
// WO is status='submitted' — the ONE dominant lifecycle dimension is
// payment.  First tab = the working set (money still owed, incl.
// partial; bot-drafts are born unpaid so they land here too).  Void
// stays out of the working tabs and is reachable via [All] + the
// Status column filter.
const WO_SEGMENTS: DataGridSegment[] = [
  {
    key: 'unpaid',
    label: 'Unpaid',
    match: (r) =>
      ['unpaid', 'partial'].includes(String(r.payment_status ?? '')) &&
      String(r.status ?? '') !== 'void',
  },
  {
    key: 'paid',
    label: 'Paid',
    match: (r) =>
      String(r.payment_status ?? '') === 'paid' &&
      String(r.status ?? '') !== 'void',
  },
  { key: 'all', label: 'All', showCount: false },
];
import { toneClasses, type Tone } from '../../lib/status';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';

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

// Reason-for-repair class → tone.  Emergency shouts (danger),
// non-scheduled warns (unplanned but not a breakdown), scheduled is
// the quiet "as-planned" green.  '' (unclassified) renders a muted
// dash rather than a pill so untagged rows don't add noise.
const PRIORITY_TONE: Record<string, Tone> = {
  scheduled:     'ok',
  non_scheduled: 'warn',
  emergency:     'danger',
};
const PRIORITY_LABEL: Record<string, string> = {
  scheduled:     'Scheduled',
  non_scheduled: 'Non-scheduled',
  emergency:     'Emergency',
};

function PriorityCell({ value }: { value: unknown }) {
  const v = String(value || '').toLowerCase();
  if (!v) return <span className="text-muted-foreground">—</span>;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium ${toneClasses(PRIORITY_TONE[v] ?? 'neutral')}`}>
      {PRIORITY_LABEL[v] ?? v}
    </span>
  );
}

function Pill({ value, palette }: { value: unknown; palette: Record<string, Tone> }) {
  const v = String(value || '').toLowerCase();
  const cls = toneClasses(palette[v] ?? 'neutral');
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium capitalize ${cls}`}>
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

// Source provenance cell.  'manual' (or empty) reads as a quiet dash;
// any integration source ('datatruck') gets an info pill so synced
// rows are distinguishable from hand-entered ones at a glance.
function SourceCell({ value }: { value: unknown }) {
  const v = String(value || 'manual').toLowerCase();
  if (v === 'manual') return <span className="text-muted-foreground">—</span>;
  const label = v.charAt(0).toUpperCase() + v.slice(1);
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium ${toneClasses('info')}`}>
      {label}
    </span>
  );
}

// Title-case a snake_case code for the filter dropdown ("in_progress"
// → "In Progress") — statuses render as tone pills in the cells but
// the dropdown wants plain text.
const titleCase = (s: string) =>
  s ? s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : '(none)';

function makeColumns(tz: string): AnyColumn[] {
  return [
  { key: 'id', label: '#', sortable: true, render: (v) => <span className="font-mono text-xs text-muted-foreground">{`#${v}`}</span> },
  { key: 'vehicle_name', label: 'Vehicle', sortable: true, filterable: true },
  // Compact company CODE on the list (the full name lives on the WO
  // detail page).  Set from the MC/DOT match during sync; '—' if none.
  { key: 'company_code', label: 'Company', sortable: true, filterable: true,
    render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  { key: 'vendor_name', label: 'Vendor', sortable: true, filterable: true,
    render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
  {
    key: 'service_date', label: 'Service Date', sortable: true,
    filterable: true, filterMode: 'date-range',
    render: (v) => v ? formatDay(String(v), { timeZone: tz }) : <span className="text-muted-foreground">—</span>,
  },
  { key: 'total_cost', label: 'Total', sortable: true,
    filterable: true, filterMode: 'range', filterRange: { min: 0, step: 100, unit: '$' },
    render: (v) => <MoneyCell value={v} /> },
  { key: 'status', label: 'Status', sortable: true, filterable: true,
    filterValue: (row) => String((row as { status?: string }).status ?? ''),
    filterLabel: (row) => titleCase(String((row as { status?: string }).status ?? '')),
    render: (v) => <Pill value={v} palette={STATUS_TONE} /> },
  // Reason-for-repair class — planned upkeep vs unplanned firefighting.
  // Filterable so an operator can isolate emergency spend; the label
  // maps '' → "Unclassified" so that bucket is filterable too.
  { key: 'repair_priority', label: 'Priority', sortable: true, filterable: true,
    filterValue: (row) => String((row as { repair_priority?: string }).repair_priority ?? ''),
    filterLabel: (row) => {
      const p = String((row as { repair_priority?: string }).repair_priority ?? '');
      return p ? (PRIORITY_LABEL[p] ?? titleCase(p)) : 'Unclassified';
    },
    render: (v) => <PriorityCell value={v} /> },
  { key: 'payment_status', label: 'Payment', sortable: true, filterable: true,
    filterValue: (row) => String((row as { payment_status?: string }).payment_status ?? ''),
    filterLabel: (row) => titleCase(String((row as { payment_status?: string }).payment_status ?? '')),
    render: (v) => <Pill value={v} palette={PAYMENT_TONE} /> },
  // Provenance — synced rows (Datatruck) read alongside hand-entered
  // ones, so the operator needs an at-a-glance "where did this come
  // from".  Manual rows show a muted dash to keep the column quiet.
  { key: 'source', label: 'Source', sortable: true, filterable: true,
    filterValue: (row) => String((row as { source?: string }).source || 'manual'),
    filterLabel: (row) => titleCase(String((row as { source?: string }).source || 'manual')),
    render: (v) => <SourceCell value={v} /> },
  // Invoice number is the operator's primary cross-reference to their
  // own bookkeeping system — surface it as a distinct column.
  { key: 'invoice_number', label: 'Invoice #', render: (v) => (v ? <span className="font-mono text-xs">{String(v)}</span> : <span className="text-muted-foreground">—</span>) },
  ];
}

export default function WorkOrders() {
  const { t } = useTranslation();
  const tz = useTimezone();
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
      // task_type filtering requires joining through linked maintenance
      // tasks — not surfaced on the work-order row directly today.
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
        <DataGrid
          tableId="work-orders"
          segments={WO_SEGMENTS}
          columns={makeColumns(tz)}
          data={workOrders as unknown as Record<string, unknown>[]}
          searchKey={['vendor_name', 'vehicle_name', 'invoice_number', 'company_code', 'complaint', 'cause', 'correction']}
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
