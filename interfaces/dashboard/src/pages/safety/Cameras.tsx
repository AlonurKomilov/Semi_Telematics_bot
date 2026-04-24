import { useEffect, useState } from 'react';
import { apiFetch, apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import type { CameraCheck, CameraChecksResponse, AnyColumn } from '../../types';

const STATUS_COLORS: Record<string, string> = {
  OK: 'bg-green-500/15 text-green-700 dark:text-green-400',
  WARNING: 'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400',
  PROBLEM: 'bg-red-500/15 text-red-700 dark:text-red-400',
};

const OBSTRUCTION_COLORS: Record<string, string> = {
  none: 'text-green-600 dark:text-green-400',
  partial: 'text-yellow-600 dark:text-yellow-400',
  full: 'text-destructive',
};

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_COLORS[status] || 'bg-gray-500/20 text-muted-foreground';
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>{status}</span>;
}

const columns: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle' },
  {
    key: 'camera_type',
    label: 'Camera',
    render: (v) => <span className="capitalize">{v as string}</span>,
  },
  {
    key: 'status',
    label: 'Status',
    render: (v) => <StatusBadge status={v as string} />,
  },
  {
    key: 'obstruction',
    label: 'Obstruction',
    render: (v) => {
      const s = v as string;
      const cls = OBSTRUCTION_COLORS[s] || 'text-muted-foreground';
      return <span className={`capitalize ${cls}`}>{s}</span>;
    },
  },
  {
    key: 'alignment',
    label: 'Alignment',
    render: (v) => <span className="capitalize">{v as string}</span>,
  },
  {
    key: 'quality',
    label: 'Quality',
    render: (v) => <span className="capitalize">{v as string}</span>,
  },
  {
    key: 'checked_at',
    label: 'Checked',
    render: (v) => v ? new Date(v as string).toLocaleString() : '—',
  },
];

export default function Cameras() {
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

  if (error && checks.length === 0) return <p className="text-destructive">{error}</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Cameras</h1>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowHistory(!showHistory)}
            className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
              showHistory
                ? 'bg-primary text-primary-foreground'
                : 'bg-muted text-muted-foreground hover:text-foreground'
            }`}
          >
            {showHistory ? '📋 All History' : '📷 Latest Only'}
          </button>
          <input
            type="text"
            placeholder="Filter by vehicle..."
            value={vehicleFilter}
            onChange={(e) => setVehicleFilter(e.target.value)}
            className="bg-muted border border-border rounded px-3 py-2 text-sm placeholder-muted-foreground focus:outline-none focus:border-ring w-56"
          />
        </div>
      </div>

      {/* Status summary */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-green-500 inline-block" />
            <p className="text-xs text-muted-foreground">OK</p>
          </div>
          <p className="text-xl font-bold mt-1">{statusCounts.OK}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-yellow-500 inline-block" />
            <p className="text-xs text-muted-foreground">Warning</p>
          </div>
          <p className="text-xl font-bold mt-1">{statusCounts.WARNING}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-red-500 inline-block" />
            <p className="text-xs text-muted-foreground">Problem</p>
          </div>
          <p className="text-xl font-bold mt-1">{statusCounts.PROBLEM}</p>
        </div>
      </div>

      {loading ? (
        <p className="text-muted-foreground">Loading...</p>
      ) : (
        <DataTable
          columns={columns}
          data={checks as unknown as Record<string, unknown>[]}
          searchKey="vehicle_name"
          onRowClick={(row) => setDetail(row as unknown as CameraCheck)}
        />
      )}

      {/* Detail drawer */}
      {detail && (
        <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={() => setDetail(null)}>
          <div
            className="w-[480px] bg-card border-l border-border p-6 overflow-y-auto"
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
                <dd className={`capitalize ${OBSTRUCTION_COLORS[detail.obstruction] || ''}`}>{detail.obstruction}</dd>
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
                <dd>{new Date(detail.checked_at).toLocaleString()}</dd>
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
