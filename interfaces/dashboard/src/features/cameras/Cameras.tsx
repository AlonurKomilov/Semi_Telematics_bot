import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Camera, Filter } from 'lucide-react';
import { apiFetch, apiJSON } from '../../api/client';
import { toneClasses, toneText, type Tone } from '../../lib/status';
import DataGrid from '../../components/datagrid';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';
import type { CameraCheck, CameraChecksResponse, AnyColumn } from '../../types';

// Camera health vocabulary → semantic tone.  Camera-check specific
// strings (not in the shared statusTone map), mapped here once.
const STATUS_TONE: Record<string, Tone> = {
  OK: 'ok',
  WARNING: 'warn',
  PROBLEM: 'danger',
};

const OBSTRUCTION_TONE: Record<string, Tone> = {
  none: 'ok',
  partial: 'warn',
  full: 'danger',
};

function StatusBadge({ status }: { status: string }) {
  const cls = toneClasses(STATUS_TONE[status] ?? 'neutral');
  return <span className={`px-2 py-0.5 rounded-md text-xs font-medium ${cls}`}>{status}</span>;
}

// Capitalise an enum code for the filter dropdown — matches the
// ``capitalize`` styling the cells use.
const capFirst = (s: string) =>
  s ? s.charAt(0).toUpperCase() + s.slice(1) : '(none)';

function makeColumns(tz: string): AnyColumn[] {
  return [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true, filterable: true },
  {
    key: 'camera_type',
    label: 'Camera',
    sortable: true,
    filterable: true,
    filterValue: (row) => String((row as { camera_type?: string }).camera_type ?? ''),
    filterLabel: (row) => capFirst(String((row as { camera_type?: string }).camera_type ?? '')),
    render: (v) => <span className="capitalize">{v as string}</span>,
  },
  {
    key: 'status',
    label: 'Status',
    sortable: true,
    filterable: true,
    filterValue: (row) => String((row as { status?: string }).status ?? ''),
    filterLabel: (row) => capFirst(String((row as { status?: string }).status ?? '')),
    render: (v) => <StatusBadge status={v as string} />,
  },
  {
    key: 'obstruction',
    label: 'Obstruction',
    sortable: true,
    filterable: true,
    filterValue: (row) => String((row as { obstruction?: string }).obstruction ?? ''),
    filterLabel: (row) => capFirst(String((row as { obstruction?: string }).obstruction ?? '')),
    render: (v) => {
      const s = v as string;
      const cls = toneText(OBSTRUCTION_TONE[s] ?? 'neutral');
      return <span className={`capitalize ${cls}`}>{s}</span>;
    },
  },
  {
    key: 'alignment',
    label: 'Alignment',
    sortable: true,
    filterable: true,
    filterValue: (row) => String((row as { alignment?: string }).alignment ?? ''),
    filterLabel: (row) => capFirst(String((row as { alignment?: string }).alignment ?? '')),
    render: (v) => <span className="capitalize">{v as string}</span>,
  },
  {
    key: 'quality',
    label: 'Quality',
    sortable: true,
    filterable: true,
    filterValue: (row) => String((row as { quality?: string }).quality ?? ''),
    filterLabel: (row) => capFirst(String((row as { quality?: string }).quality ?? '')),
    render: (v) => <span className="capitalize">{v as string}</span>,
  },
  {
    key: 'checked_at',
    label: 'Checked',
    sortable: true,
    filterable: true, filterMode: 'date-range',
    render: (v) => v ? formatDate(v as string, { timeZone: tz }) : '—',
  },
  ];
}

export default function Cameras() {
  const { t } = useTranslation();
  const tz = useTimezone();
  const [checks, setChecks] = useState<CameraCheck[]>([]);
  const [vehicleFilter, setVehicleFilter] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState<CameraCheck | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imageLoading, setImageLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams();
    if (vehicleFilter) params.set('vehicle', vehicleFilter);
    if (!showHistory) params.set('latest_only', 'true');
    else params.set('latest_only', 'false');

    apiJSON<CameraChecksResponse>(`/safety/cameras?${params}`)
      .then((d) => setChecks(d.checks || []))
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, [vehicleFilter, showHistory]);

  // Load camera image when detail changes
  useEffect(() => {
    if (!detail) {
      if (imageUrl) { URL.revokeObjectURL(imageUrl); setImageUrl(null); }
      return;
    }
    if (!detail.image_path) { setImageUrl(null); return; }
    setImageLoading(true);
    apiFetch(`/safety/cameras/${detail.id}/image`)
      .then((res) => {
        if (!res.ok) throw new Error('No image');
        return res.blob();
      })
      .then((blob) => setImageUrl(URL.createObjectURL(blob)))
      .catch(() => setImageUrl(null))
      .finally(() => setImageLoading(false));
    return () => { if (imageUrl) URL.revokeObjectURL(imageUrl); };
  }, [detail?.id]);

  // Status summary
  const statusCounts: Record<string, number> = { OK: 0, WARNING: 0, PROBLEM: 0 };
  checks.forEach((c) => { statusCounts[c.status] = (statusCounts[c.status] || 0) + 1; });

  if (error && checks.length === 0) {
    return (
      <div>
        <PageHeader
          icon={Camera}
          title={t('pages.cameras_title')}
          description={t('pages.cameras_desc_short')}
        />
        <ErrorState message={error} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        icon={Camera}
        title={t('pages.cameras_title')}
        description={t('pages.cameras_desc_long')}
        actions={
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowHistory(!showHistory)}
              className={`inline-flex items-center gap-1 px-3 py-1.5 rounded-md text-xs font-medium transition border ${
                showHistory
                  ? 'bg-primary text-primary-foreground border-primary'
                  : 'bg-background border-border text-muted-foreground hover:text-foreground'
              }`}
            >
              <Filter size={12} />
              {showHistory ? 'All history' : 'Latest only'}
            </button>
            <input
              type="text"
              placeholder="Filter by vehicle…"
              value={vehicleFilter}
              onChange={(e) => setVehicleFilter(e.target.value)}
              className="bg-background border border-border rounded-md px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:border-ring w-48"
            />
          </div>
        }
      />

      {/* Status summary */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-ok inline-block" />
            <p className="text-xs text-muted-foreground">OK</p>
          </div>
          <p className="text-xl font-bold mt-1">{statusCounts.OK}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-warn inline-block" />
            <p className="text-xs text-muted-foreground">Warning</p>
          </div>
          <p className="text-xl font-bold mt-1">{statusCounts.WARNING}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-danger inline-block" />
            <p className="text-xs text-muted-foreground">Problem</p>
          </div>
          <p className="text-xl font-bold mt-1">{statusCounts.PROBLEM}</p>
        </div>
      </div>

      {loading ? (
        <TableSkeleton rows={6} cols={5} />
      ) : checks.length === 0 ? (
        <EmptyState
          icon={Camera}
          title={vehicleFilter ? 'No cameras match this filter' : 'No camera checks yet'}
          description="Camera checks accrue automatically once Samsara dashcams report status. Try widening the filter."
        />
      ) : (
        <DataGrid
          tableId="camera-checks"
          columns={makeColumns(tz)}
          data={checks as unknown as Record<string, unknown>[]}
          searchKey="vehicle_name"
          onRowClick={(row) => setDetail(row as unknown as CameraCheck)}
        />
      )}

      {/* Detail drawer */}
      {detail && (
        <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={() => setDetail(null)}>
          <div
            className="w-full max-w-lg bg-card border-l border-border p-6 overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">{detail.vehicle_name}</h2>
              <button onClick={() => setDetail(null)} className="text-muted-foreground hover:text-foreground">✕</button>
            </div>

            {/* Camera screenshot */}
            <div className="mb-4 rounded-lg overflow-hidden bg-muted border border-border">
              {imageLoading ? (
                <div className="flex items-center justify-center h-48 text-muted-foreground">
                  <svg className="animate-spin h-6 w-6 mr-2" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                  Loading image…
                </div>
              ) : imageUrl ? (
                <img
                  src={imageUrl}
                  alt={`Camera ${detail.camera_type} — ${detail.vehicle_name}`}
                  className="w-full h-auto"
                />
              ) : (
                <div className="flex items-center justify-center h-48 text-muted-foreground">
                  <span>📷 No screenshot available</span>
                </div>
              )}
            </div>

            <dl className="space-y-3 text-sm">
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Camera</dt>
                <dd className="capitalize">{detail.camera_type}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Status</dt>
                <dd><StatusBadge status={detail.status} /></dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Obstruction</dt>
                <dd className={`capitalize ${OBSTRUCTION_TONE[detail.obstruction] ? toneText(OBSTRUCTION_TONE[detail.obstruction]) : ''}`}>{detail.obstruction}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Alignment</dt>
                <dd className="capitalize">{detail.alignment}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Quality</dt>
                <dd className="capitalize">{detail.quality}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Checked</dt>
                <dd>{formatDate(detail.checked_at, { timeZone: tz })}</dd>
              </div>
              {detail.summary && (
                <div className="pt-2 border-t border-border">
                  <dt className="text-muted-foreground mb-1">AI Summary</dt>
                  <dd className="text-foreground/80">{detail.summary}</dd>
                </div>
              )}
            </dl>
          </div>
        </div>
      )}

      <p className="text-xs text-muted-foreground mt-2">{checks.length} check{checks.length !== 1 ? 's' : ''}</p>
    </div>
  );
}
