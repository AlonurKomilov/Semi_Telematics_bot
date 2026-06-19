import { useEffect, useMemo, useState, lazy, Suspense } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ClipboardCheck, Plus, Search, X } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { toneClasses } from '../../lib/status';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';
import DataTable from '../../components/DataTable';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import type { AnyColumn, PTIInspectionRow, PTIInspectionsResponse } from '../../types';
import { InspectionDetail } from './InspectionDetail';
import { NewInspectionDialog } from './NewInspectionDialog';

// Template editor is lazy-loaded so the default "Submissions" tab
// doesn't pay for the editor's JS (forms, modal, dnd) until the
// fleet clicks the second tab.  Inline rather than via the router
// because the editor is part of the fleet's PTI workflow, not a
// separate admin page.
const TemplateEditorInline = lazy(() => import('./TemplateEditor'));

/**
 * Fleet-side PTI list.  Mirrors the layout of the maintenance Tasks
 * page so the visual grammar (filter chips, DataTable, right-side
 * drawer) feels identical across the two compliance modules.
 *
 * Filters: All · Pending Review · Approved · Needs Service · Rejected ·
 * Overdue.  Click a row to open the ``<InspectionDetail>`` drawer.
 *
 * Driver / Vehicle / Submitted / Defects / Status / Reviewer columns.
 */

// ── Status badge ──────────────────────────────────────────────────

const REVIEW_STATUS_LABELS: Record<string, string> = {
  approved:          'Approved',
  needs_service:     'Needs Service',
  rejected:          'Rejected',
  revision_required: 'Revision Required',
};

const WORKFLOW_STATUS_LABELS: Record<string, string> = {
  scheduled:         'Scheduled',
  in_progress:       'In Progress',
  submitted:         'Awaiting Review',
  reviewed:          'Reviewed',
  revision_required: 'Revision Required',
};

function StatusChip({ row }: { row: PTIInspectionRow }) {
  // Review outcome wins over workflow status — a reviewed-approved
  // row shows "Approved" not "Reviewed".
  if (row.review_status) {
    const label = REVIEW_STATUS_LABELS[row.review_status] ?? row.review_status;
    const cls = toneClasses(
      row.review_status === 'approved' ? 'ok'     :
      row.review_status === 'rejected' ? 'danger' :
                                         'warn'   // needs_service / revision_required
    );
    return <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium ${cls}`}>{label}</span>;
  }
  const wf = WORKFLOW_STATUS_LABELS[row.status] ?? row.status;
  const cls = toneClasses(
    row.status === 'submitted'         ? 'info' :
    row.status === 'revision_required' ? 'warn' :
                                         'neutral'
  );
  return <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium ${cls}`}>{wf}</span>;
}


// ── Cells ─────────────────────────────────────────────────────────

function DefectsCell({ row }: { row: PTIInspectionRow }) {
  if (row.defects_count === 0) {
    return <span className="text-muted-foreground">0</span>;
  }
  const oosBadge = row.has_oos_defect ? (
    <span className={`ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded border text-3xs font-medium ${toneClasses('danger')}`}>
      OOS
    </span>
  ) : null;
  return (
    <span className="font-medium tabular-nums">
      {row.defects_count}
      {oosBadge}
    </span>
  );
}

function _formatDate(iso: string | null, tz?: string): string {
  return formatDay(iso, { timeZone: tz, intl: { year: 'numeric', month: 'short', day: 'numeric' } });
}


// ── Columns ───────────────────────────────────────────────────────

const buildColumns = (tz?: string): AnyColumn[] => [
  { key: 'user_id',       label: 'Driver', sortable: true,
    render: (v) => <span className="font-mono text-xs">user {String(v ?? '—')}</span> },
  { key: 'vehicle_name',  label: 'Vehicle', sortable: true,
    render: (v) => <span className="font-mono">{String(v ?? '—')}</span> },
  { key: 'inspection_type', label: 'Type', sortable: true,
    render: (v) => <span className="capitalize">{String(v ?? '—')}</span> },
  { key: 'submitted_at',  label: 'Submitted', sortable: true,
    render: (v) => _formatDate(typeof v === 'string' ? v : null, tz) },
  { key: 'due_by',        label: 'Due', sortable: true,
    render: (v) => _formatDate(typeof v === 'string' ? v : null, tz) },
  { key: 'defects_count', label: 'Defects',
    render: (_v, row) => <DefectsCell row={row as PTIInspectionRow} /> },
  { key: 'status',        label: 'Status', sortable: true,
    render: (_v, row) => <StatusChip row={row as PTIInspectionRow} /> },
  { key: 'reviewed_at',   label: 'Reviewed',
    render: (v) => _formatDate(typeof v === 'string' ? v : null, tz) },
];


// ── Page ──────────────────────────────────────────────────────────

type FilterKey = '' | 'submitted' | 'approved' | 'needs_service' | 'rejected' | 'overdue' | 'not_started';
type TopTab = 'submissions' | 'template';


export default function Inspections() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const tz = useTimezone();
  const columns = useMemo(() => buildColumns(tz), [tz]);
  const [topTab, setTopTab] = useState<TopTab>(() => {
    // Honour ?tab=template so existing /admin/inspection-template
    // bookmarks land on the right tab via the alias redirect.
    if (typeof window !== 'undefined') {
      const p = new URLSearchParams(window.location.search);
      return p.get('tab') === 'template' ? 'template' : 'submissions';
    }
    return 'submissions';
  });
  const [filter, setFilter] = useState<FilterKey>('');
  // ``?vehicle=NAME`` from the Vehicle Detail "View all" deep-link
  // pre-fills the search box and shows a sticky banner so the fleet
  // knows they're in a vehicle-scoped view.
  const initialVehicle = (() => {
    if (typeof window === 'undefined') return '';
    return new URLSearchParams(window.location.search).get('vehicle') ?? '';
  })();
  const [search, setSearch] = useState(initialVehicle);
  const [lockedVehicle, setLockedVehicle] = useState(initialVehicle);
  const [showNewDialog, setShowNewDialog] = useState(false);
  const [selected, setSelected] = useState<PTIInspectionRow | null>(null);

  // Clear the vehicle scope (URL param + search + banner).
  const clearVehicleFilter = () => {
    setLockedVehicle('');
    setSearch('');
    if (typeof window !== 'undefined') {
      const url = new URL(window.location.href);
      url.searchParams.delete('vehicle');
      window.history.replaceState({}, '', url.toString());
    }
  };

  // Single fetch — the API supports filtering server-side; we pass
  // status/review_status depending on which chip the user picked.
  const queryParams = useMemo(() => {
    const params = new URLSearchParams();
    params.set('page_size', '200');
    params.set('days', '90');
    if (filter === 'submitted') params.set('status', 'submitted');
    else if (filter === 'approved' || filter === 'needs_service' || filter === 'rejected') {
      params.set('review_status', filter);
    }
    return params.toString();
  }, [filter]);

  const { data, isLoading, error: queryError } = useQuery({
    queryKey: ['pti-inspections', queryParams],
    queryFn: () => apiJSON<PTIInspectionsResponse>(`/inspections?${queryParams}`),
    placeholderData: (prev) => prev,
  });

  // Honour ?id=NNN from the bot's submission-ping deep-link.
  // The fleet receives a Telegram message with a "Review" button that
  // points at /inspections?id=42 — landing there should auto-open the
  // detail drawer instead of just showing the list.  Once the data is
  // loaded, find the matching row and select it.
  useEffect(() => {
    if (typeof window === 'undefined' || !data) return;
    const p = new URLSearchParams(window.location.search);
    const idStr = p.get('id');
    if (!idStr) return;
    const id = Number(idStr);
    if (!Number.isFinite(id)) return;
    const row = data.items.find(r => r.id === id);
    if (row && (!selected || selected.id !== id)) {
      setSelected(row);
    }
  }, [data, selected]);

  const rows: PTIInspectionRow[] = useMemo(() => {
    let items = data?.items ?? [];

    // Bucket filters that the server doesn't enforce.
    if (filter === 'overdue') {
      const now = new Date().toISOString();
      items = items.filter(r =>
        r.due_by != null
        && r.due_by < now
        && ['scheduled', 'in_progress', 'revision_required'].includes(r.status),
      );
    } else if (filter === 'not_started') {
      // ``not_started`` = scheduled and untouched by the driver.
      // The server returns the status filter, but we additionally
      // need to know nothing in the wizard was started.  For the MVP
      // we treat ``status === 'scheduled'`` as the signal — the
      // wizard flips to ``in_progress`` on the first item touch.
      items = items.filter(r => r.status === 'scheduled');
    }

    // Free-text search across driver name + vehicle.  ``driver_name``
    // isn't on the list row by default (it's only on the detail
    // endpoint), so we fall back to ``user_id`` for now — search by
    // vehicle is the higher-signal use case anyway.
    const q = search.trim().toLowerCase();
    if (q) {
      items = items.filter(r =>
        (r.vehicle_name || '').toLowerCase().includes(q)
        || String(r.user_id).includes(q),
      );
    }
    return items;
  }, [data, filter, search]);

  // Counts for the chips — same buckets as the filter.  Pulled from
  // the same fetched payload so we don't fan-out 7 separate queries.
  const counts = useMemo(() => {
    const all = data?.items ?? [];
    const now = new Date().toISOString();
    return {
      all:           all.length,
      submitted:     all.filter(r => r.status === 'submitted').length,
      approved:      all.filter(r => r.review_status === 'approved').length,
      needs_service: all.filter(r => r.review_status === 'needs_service').length,
      rejected:      all.filter(r => r.review_status === 'rejected').length,
      overdue:       all.filter(r =>
        r.due_by != null && r.due_by < now
        && ['scheduled', 'in_progress', 'revision_required'].includes(r.status),
      ).length,
      not_started:   all.filter(r => r.status === 'scheduled').length,
    };
  }, [data]);

  // Refresh after the detail drawer's "Submit Review" succeeds.
  const refresh = () => qc.invalidateQueries({ queryKey: ['pti-inspections'] });

  // Escape closes the detail drawer.
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape' && selected) setSelected(null); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [selected]);

  const fetchError = queryError instanceof Error ? queryError.message : '';

  return (
    <div>
      <PageHeader
        icon={ClipboardCheck}
        title={t('pages.inspections_title')}
        description={t('pages.inspections_desc')}
        actions={
          <button
            type="button"
            onClick={() => setShowNewDialog(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary/90 rounded-md text-xs font-medium text-primary-foreground transition"
          >
            <Plus size={14} />
            {t('inspections.new.button')}
          </button>
        }
      />

      {/* Top-level tabs — Submissions (the review queue) vs Template
          (the editable checklist).  Both are fleet's responsibility
          so they sit together rather than splitting the template
          editor off to an admin route. */}
      <div className="inline-flex gap-1 mb-4 p-1 bg-muted/40 rounded-md">
        {([
          { key: 'submissions', label: t('inspections.tab_submissions') },
          { key: 'template',    label: t('inspections.tab_template') },
        ] as Array<{ key: TopTab; label: string }>).map(tab => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setTopTab(tab.key)}
            className={`px-3 py-1.5 text-sm rounded-md transition ${
              topTab === tab.key
                ? 'bg-card text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {topTab === 'template' && (
        <Suspense fallback={<TableSkeleton rows={6} cols={1} />}>
          <TemplateEditorInline />
        </Suspense>
      )}

      {topTab === 'submissions' && <>
      {/* Vehicle-scoped banner — shown when arriving from
          /vehicles/:name → "View all".  Sticky so it survives the
          full scroll of the table. */}
      {lockedVehicle && (
        <div className="mb-3 inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-primary/10 border border-primary/20 text-sm">
          <span className="text-primary font-medium">
            {t('inspections.filter_banner.vehicle_label', { vehicle: lockedVehicle })}
          </span>
          <button
            type="button"
            onClick={clearVehicleFilter}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <X size={12} />
            {t('inspections.filter_banner.clear')}
          </button>
        </div>
      )}

      {/* Search input — by driver id or vehicle name */}
      <div className="relative mb-3 max-w-sm">
        <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder={t('inspections.search_placeholder')}
          className="w-full pl-8 pr-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:border-ring"
        />
      </div>

      {/* Filter chips with bucket counts */}
      <div className="flex flex-wrap gap-2 mb-4">
        {([
          { key: '',              label: 'All',            count: counts.all,           dot: 'bg-muted-foreground/40' },
          { key: 'not_started',   label: 'Not Started',    count: counts.not_started,   dot: 'bg-muted-foreground' },
          { key: 'submitted',     label: 'Pending Review', count: counts.submitted,     dot: 'bg-info' },
          { key: 'approved',      label: 'Approved',       count: counts.approved,      dot: 'bg-ok' },
          { key: 'needs_service', label: 'Needs Service',  count: counts.needs_service, dot: 'bg-warn' },
          { key: 'rejected',      label: 'Rejected',       count: counts.rejected,      dot: 'bg-danger' },
          { key: 'overdue',       label: 'Overdue',        count: counts.overdue,       dot: 'bg-warn' },
        ] as const).map(chip => {
          const active = filter === chip.key;
          return (
            <button
              key={chip.key || 'all'}
              onClick={() => setFilter(active ? '' : chip.key)}
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition border ${
                active
                  ? 'bg-primary text-primary-foreground border-primary'
                  : 'bg-card hover:bg-muted text-foreground border-border'
              }`}
            >
              <span className={`w-2 h-2 rounded-full ${chip.dot}`} />
              {chip.label}
              <span className={`tabular-nums ${active ? 'opacity-80' : 'text-muted-foreground'}`}>
                {chip.count}
              </span>
            </button>
          );
        })}
      </div>

      {fetchError && (
        <div className="mb-3">
          <ErrorState message={fetchError} />
        </div>
      )}

      {isLoading && rows.length === 0 ? (
        <TableSkeleton rows={6} cols={7} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={ClipboardCheck}
          title={filter ? `No ${filter.replace(/_/g, ' ')} inspections` : 'No inspections yet'}
          description="Drivers submit PTIs from the Mini App.  Once they land, you can review them here."
        />
      ) : (
        <>
          <DataTable
            columns={columns}
            data={rows as unknown as Record<string, unknown>[]}
            searchKey="vehicle_name"
            onRowClick={(row) => setSelected(row as unknown as PTIInspectionRow)}
          />
          <p className="text-xs text-muted-foreground mt-2">
            {rows.length} inspection{rows.length !== 1 ? 's' : ''}
            {filter && data && data.items.length !== rows.length
              ? ` · ${data.items.length - rows.length} hidden by filter`
              : ''}
          </p>
        </>
      )}
      </>}

      {selected && (
        <InspectionDetail
          inspectionId={selected.id}
          onClose={() => setSelected(null)}
          onReviewed={() => { refresh(); setSelected(null); }}
          onResent={refresh}
        />
      )}

      {showNewDialog && (
        <NewInspectionDialog
          onCreated={refresh}
          onClose={() => setShowNewDialog(false)}
        />
      )}
    </div>
  );
}
