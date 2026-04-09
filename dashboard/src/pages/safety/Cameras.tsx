import { useEffect, useState } from 'react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import type { CameraCheck, CameraChecksResponse, AnyColumn } from '../../types';

const STATUS_COLORS: Record<string, string> = {
  OK: 'bg-green-500/20 text-green-400',
  WARNING: 'bg-yellow-500/20 text-yellow-400',
  PROBLEM: 'bg-red-500/20 text-red-400',
};

const OBSTRUCTION_COLORS: Record<string, string> = {
  none: 'text-green-400',
  minor: 'text-yellow-400',
  significant: 'text-orange-400',
  critical: 'text-red-400',
};

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_COLORS[status] || 'bg-gray-500/20 text-gray-400';
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
      const cls = OBSTRUCTION_COLORS[s] || 'text-gray-400';
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState<CameraCheck | null>(null);

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams();
    if (vehicleFilter) params.set('vehicle', vehicleFilter);

    apiJSON<CameraChecksResponse>(`/safety/cameras?${params}`)
      .then((d) => setChecks(d.checks || []))
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, [vehicleFilter]);

  // Status summary
  const statusCounts: Record<string, number> = { OK: 0, WARNING: 0, PROBLEM: 0 };
  checks.forEach((c) => { statusCounts[c.status] = (statusCounts[c.status] || 0) + 1; });

  if (error && checks.length === 0) return <p className="text-red-400">{error}</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Camera Checks</h1>
        <input
          type="text"
          placeholder="Filter by vehicle..."
          value={vehicleFilter}
          onChange={(e) => setVehicleFilter(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm placeholder-gray-500 focus:outline-none focus:border-blue-500 w-56"
        />
      </div>

      {/* Status summary */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-green-500 inline-block" />
            <p className="text-xs text-gray-400">OK</p>
          </div>
          <p className="text-xl font-bold mt-1">{statusCounts.OK}</p>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-yellow-500 inline-block" />
            <p className="text-xs text-gray-400">Warning</p>
          </div>
          <p className="text-xl font-bold mt-1">{statusCounts.WARNING}</p>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-red-500 inline-block" />
            <p className="text-xs text-gray-400">Problem</p>
          </div>
          <p className="text-xl font-bold mt-1">{statusCounts.PROBLEM}</p>
        </div>
      </div>

      {loading ? (
        <p className="text-gray-500">Loading...</p>
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
            className="w-96 bg-gray-900 border-l border-gray-800 p-6 overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">{detail.vehicle_name}</h2>
              <button onClick={() => setDetail(null)} className="text-gray-500 hover:text-white">✕</button>
            </div>
            <dl className="space-y-3 text-sm">
              <div className="flex justify-between">
                <dt className="text-gray-400">Camera</dt>
                <dd className="capitalize">{detail.camera_type}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-400">Status</dt>
                <dd><StatusBadge status={detail.status} /></dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-400">Obstruction</dt>
                <dd className={`capitalize ${OBSTRUCTION_COLORS[detail.obstruction] || ''}`}>{detail.obstruction}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-400">Alignment</dt>
                <dd className="capitalize">{detail.alignment}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-400">Quality</dt>
                <dd className="capitalize">{detail.quality}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-400">Checked</dt>
                <dd>{new Date(detail.checked_at).toLocaleString()}</dd>
              </div>
              {detail.summary && (
                <div className="pt-2 border-t border-gray-800">
                  <dt className="text-gray-400 mb-1">AI Summary</dt>
                  <dd className="text-gray-300">{detail.summary}</dd>
                </div>
              )}
            </dl>
          </div>
        </div>
      )}

      <p className="text-xs text-gray-500 mt-2">{checks.length} check{checks.length !== 1 ? 's' : ''}</p>
    </div>
  );
}
