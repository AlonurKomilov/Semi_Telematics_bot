/**
 * Fleet-wide Documents — every truck's paperwork in one place.
 *
 * Answers what the per-truck card structurally cannot: "which papers
 * expire this month, and which trucks do I go to."  Expiry is a
 * fleet-wide question — a DOT audit asks for every registration at
 * once, and the expiry alert says "3 trucks" without being able to
 * name them anywhere.  This is where that alert lands.
 *
 * Read + locate, plus download.  Uploading and deleting stay on the
 * truck's own card, which already owns those actions and their
 * confirmations — same split as Inventory's page-and-card.
 */
import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { FileText, Upload } from 'lucide-react';

import { apiFetch, apiJSON } from '../../../api/client';
import DataGrid, { type DataGridSegment } from '../../../components/datagrid';
import { PageHeader, CardSkeleton, ErrorState } from '../../../components/shell';
import { toneText } from '../../../lib/status';
import { Button } from '../../../components/ui/button';
import { useViewPermissions } from '../../../hooks/useViewPermissions';
import { typeLabel } from './docTypes';
import UploadDocumentDialog, { type UploadTargetVehicle } from './UploadDocumentDialog';
import type { AnyColumn } from '../../../types';

interface FleetDoc {
  id: number;
  status?: string;
  vehicle_id: number;
  doc_type: string;
  file_name: string;
  file_size: number | null;
  issued_at: string | null;
  expires_at: string | null;
  uploaded_at: string;
  unit_number: string;
  company_code: string;
}

/** Whole days from today to a YYYY-MM-DD, or null when unreadable. */
function daysUntil(iso: string | null): number | null {
  if (!iso) return null;
  const day = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(day.getTime())) return null;
  const now = new Date();
  return Math.round(
    (day.getTime() - Date.UTC(
      now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(),
    )) / 86_400_000,
  );
}

async function openDoc(id: number) {
  // Through apiFetch for the bearer header, then a blob URL — a plain
  // href would arrive unauthenticated.
  const res = await apiFetch(`/vehicles/documents/${id}/download`, {});
  if (!res.ok) return;
  const url = URL.createObjectURL(await res.blob());
  window.open(url, '_blank', 'noopener');
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

const COLUMNS: AnyColumn[] = [
  {
    key: 'unit_number', label: 'Vehicle', sortable: true, filterable: true,
    render: (v, row) => (
      <Link
        to={`/vehicles/${encodeURIComponent(String(v ?? ''))}?company=${
          encodeURIComponent((row as unknown as FleetDoc).company_code ?? '')}`}
        className="text-primary hover:underline"
        onClick={(e) => e.stopPropagation()}
      >
        {String(v ?? '')}
      </Link>
    ),
  },
  { key: 'company_code', label: 'Company', sortable: true, filterable: true },
  {
    key: 'doc_type', label: 'Type', sortable: true, filterable: true,
    filterValue: (row) => String((row as unknown as FleetDoc).doc_type ?? ''),
    filterLabel: (row) => {
      const t = String((row as unknown as FleetDoc).doc_type ?? '');
      return typeLabel(t);
    },
    render: (v) => typeLabel(String(v ?? '')),
  },
  {
    key: 'file_name', label: 'File', sortable: true,
    render: (v, row) => (
      <button
        type="button"
        className="inline-flex items-center gap-1.5 text-left min-h-tap hover:underline"
        onClick={(e) => {
          e.stopPropagation();
          void openDoc((row as unknown as FleetDoc).id);
        }}
      >
        <FileText className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate">{String(v ?? '')}</span>
      </button>
    ),
  },
  {
    key: 'expires_at', label: 'Expires', sortable: true,
    filterMode: 'date-range', filterable: true,
    // The whole reason this page exists reads in one column: a lapsed
    // certificate must not look like a current one.
    render: (v) => {
      const iso = v ? String(v) : '';
      const d = daysUntil(iso);
      if (d === null) return <span className="text-muted-foreground">—</span>;
      const cls = d < 0 ? toneText('danger')
        : d <= 30 ? toneText('warn') : 'text-muted-foreground';
      const text = d < 0 ? `expired ${iso}`
        : d === 0 ? 'expires today'
        : d <= 30 ? `${iso} · in ${d}d` : iso;
      return <span className={cls}>{text}</span>;
    },
  },
  { key: 'issued_at', label: 'Issued', sortable: true },
  { key: 'uploaded_at', label: 'Uploaded', sortable: true },
];

// Lifecycle tabs the expiry alert lands on: an alert saying "3 trucks
// have papers expiring" now has a destination that shows the three.
const isArchived = (r: unknown) =>
  (r as FleetDoc).status === 'archived';

const SEGMENTS: DataGridSegment[] = [
  // `!isArchived` on every live tab, INCLUDING All — the same rule the
  // Vehicles grid follows for retired trucks.  Without it every count
  // grows each time somebody files a renewal.
  { key: 'all', label: 'All', match: (r) => !isArchived(r) },
  {
    key: 'expiring',
    label: 'Expiring',
    match: (r) => {
      if (isArchived(r)) return false;
      const d = daysUntil((r as unknown as FleetDoc).expires_at);
      return d !== null && d >= 0 && d <= 30;
    },
  },
  {
    key: 'expired',
    label: 'Expired',
    match: (r) => {
      if (isArchived(r)) return false;
      const d = daysUntil((r as unknown as FleetDoc).expires_at);
      return d !== null && d < 0;
    },
  },
  {
    key: 'no_expiry',
    label: 'No expiry set',
    // Not an error — a title never expires.  But an insurance
    // certificate with no date is a gap the alert can never see, and
    // this is the only place it is visible at all.
    match: (r) => !isArchived(r) && !(r as unknown as FleetDoc).expires_at,
  },
  {
    key: 'archived',
    label: 'Archived',
    // Superseded papers, kept because last year's registration still
    // proves the truck was legal last year — which it can only do if
    // somebody can find it.
    match: isArchived,
  },
];

export default function DocumentsPage() {
  const qc = useQueryClient();
  const { data, isLoading, error, refetch } = useQuery<{ documents: FleetDoc[] }>({
    queryKey: ['fleet-documents'],
    queryFn: () => apiJSON('/vehicles/documents?include_archived=true'),
  });

  const rows = useMemo(() => data?.documents ?? [], [data]);
  const { has } = useViewPermissions();
  const canManage = has('can_manage_vehicle_docs');
  const [uploadOpen, setUploadOpen] = useState(false);

  // The trucks a document can be filed against.  Fetched only when the
  // dialog can actually open — a read-only visitor never pays for it.
  const { data: fleet } = useQuery<{ vehicles: UploadTargetVehicle[] }>({
    queryKey: ['vehicles-for-documents'],
    queryFn: () => apiJSON('/vehicles/'),
    enabled: canManage,
    staleTime: 5 * 60 * 1000,
  });
  const targets = useMemo(
    () => (fleet?.vehicles ?? [])
      .filter((v) => v.registry_id != null)
      .sort((a, b) => String(a.name).localeCompare(String(b.name), undefined,
                                                   { numeric: true })),
    [fleet],
  );

  if (isLoading) {
    return (
      <div>
        <PageHeader icon={FileText} title="Documents" />
        <CardSkeleton height="h-64" />
      </div>
    );
  }
  if (error) {
    return (
      <div>
        <PageHeader icon={FileText} title="Documents" />
        <ErrorState message={error instanceof Error ? error.message : 'Failed to load'} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        icon={FileText}
        title="Documents"
        description="Registration, cab card, insurance and annual inspections across every truck. Deleting stays on each vehicle's own page."
        actions={canManage ? (
          <Button type="button" size="sm" onClick={() => setUploadOpen(true)}>
            <Upload />
            Upload
          </Button>
        ) : undefined}
      />
      <DataGrid
        tableId="fleet-documents"
        columns={COLUMNS}
        data={rows as unknown as Record<string, unknown>[]}
        segments={SEGMENTS}
        searchKey={['file_name', 'unit_number', 'company_code']}
        emptyMessage="No documents uploaded yet."
      />
      {canManage && (
        <UploadDocumentDialog
          open={uploadOpen}
          onClose={() => setUploadOpen(false)}
          onUploaded={() => {
            void refetch();
            // The truck's own card holds the same document.
            void qc.invalidateQueries({ queryKey: ['vehicle-documents'] });
          }}
          vehicles={targets}
        />
      )}
    </div>
  );
}
