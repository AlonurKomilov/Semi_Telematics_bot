import { useEffect, useState, useCallback } from 'react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
import type { Alert, AlertsResponse, BulkAckResponse } from '../../types';
import type { AnyColumn } from '../../types';

const ALERT_TYPES = ['all', 'fault', 'health', 'fuel', 'events', 'parking'] as const;

function TypeBadge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    fault: 'bg-orange-500/20 text-orange-400',
    health: 'bg-red-500/20 text-red-400',
    fuel: 'bg-yellow-500/20 text-yellow-400',
    events: 'bg-purple-500/20 text-purple-400',
    parking: 'bg-cyan-500/20 text-cyan-400',
  };
  const cls = colors[type] || 'bg-gray-500/20 text-gray-400';
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>{type}</span>;
}

const historyColumns: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle' },
  {
    key: 'alert_type',
    label: 'Type',
    render: (v) => <TypeBadge type={v as string} />,
  },
  {
    key: 'status',
    label: 'Status',
    render: (v) => <StatusBadge status={v as string} />,
  },
  {
    key: 'escalation_level',
    label: 'Escalation',
    render: (v) => (v as number) > 0 ? `Level ${v}` : '—',
  },
  {
    key: 'created_at',
    label: 'Created',
    render: (v) => v ? new Date(v as string).toLocaleString() : '—',
  },
  {
    key: 'acknowledged_at',
    label: 'Acknowledged',
    render: (v) => v ? new Date(v as string).toLocaleString() : '—',
  },
];

export default function Alerts() {
  const [tab, setTab] = useState<'pending' | 'history'>('pending');
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [selected, setSelected] = useState<Set<string | number>>(new Set());
  const [typeFilter, setTypeFilter] = useState('all');
  const [vehicleSearch, setVehicleSearch] = useState('');
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [acking, setAcking] = useState(false);

  const fetchAlerts = useCallback(() => {
    setLoading(true);
    setError('');
    const params = new URLSearchParams();
    if (typeFilter !== 'all') params.set('alert_type', typeFilter);
    if (vehicleSearch) params.set('vehicle', vehicleSearch);
    if (tab === 'history') params.set('days', String(days));

    const path = tab === 'pending' ? '/alerts/pending' : '/alerts/history';
    const qs = params.toString();
    apiJSON<AlertsResponse>(`${path}${qs ? `?${qs}` : ''}`)
      .then((d) => { setAlerts(d.alerts || []); setSelected(new Set()); })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load alerts'))
      .finally(() => setLoading(false));
  }, [tab, typeFilter, vehicleSearch, days]);

  useEffect(() => { fetchAlerts(); }, [fetchAlerts]);

  async function ackSelected() {
    if (selected.size === 0) return;
    setAcking(true);
    try {
      const ids = Array.from(selected).map(Number);
      await apiJSON<BulkAckResponse>('/alerts/bulk-ack', {
        method: 'POST',
        body: { ids },
      });
      setAlerts((prev) => prev.filter((a) => !selected.has(a.id)));
      setSelected(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Bulk acknowledge failed');
    } finally {
      setAcking(false);
    }
  }

  function toggleSelect(id: string | number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  if (error && alerts.length === 0) return <p className="text-red-400">{error}</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Alerts</h1>
        <div className="flex items-center gap-3">
          {tab === 'pending' && selected.size > 0 && (
            <button
              onClick={ackSelected}
              disabled={acking}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg text-sm font-medium transition"
            >
              {acking ? 'Acknowledging...' : `Acknowledge (${selected.size})`}
            </button>
          )}
          <button
            onClick={fetchAlerts}
            className="text-sm text-gray-400 hover:text-white transition"
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4">
        {(['pending', 'history'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition capitalize ${
              tab === t ? 'bg-gray-700 text-white' : 'text-gray-400 hover:text-white'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Filters bar */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="flex gap-1">
          {ALERT_TYPES.map((at) => (
            <button
              key={at}
              onClick={() => setTypeFilter(at)}
              className={`text-xs px-2.5 py-1 rounded capitalize ${
                typeFilter === at ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
              }`}
            >
              {at}
            </button>
          ))}
        </div>
        <input
          type="text"
          placeholder="Filter by vehicle..."
          value={vehicleSearch}
          onChange={(e) => setVehicleSearch(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded px-2.5 py-1 text-sm placeholder-gray-500 focus:outline-none focus:border-blue-500 w-48"
        />
        {tab === 'history' && (
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-300"
          >
            {[7, 14, 30, 60, 90].map((d) => (
              <option key={d} value={d}>{d} days</option>
            ))}
          </select>
        )}
      </div>

      {loading && alerts.length === 0 ? (
        <p className="text-gray-500">Loading...</p>
      ) : tab === 'pending' ? (
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-900 text-gray-400 text-left">
                <th className="px-4 py-3 w-8">
                  <input
                    type="checkbox"
                    checked={selected.size === alerts.length && alerts.length > 0}
                    onChange={() => {
                      if (selected.size === alerts.length) setSelected(new Set());
                      else setSelected(new Set(alerts.map((a) => a.id)));
                    }}
                  />
                </th>
                <th className="px-4 py-3">Vehicle</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Escalation</th>
                <th className="px-4 py-3">Time</th>
              </tr>
            </thead>
            <tbody>
              {alerts.length === 0 && (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-500">No pending alerts</td></tr>
              )}
              {alerts.map((a) => (
                <tr key={a.id} className="border-t border-gray-800 hover:bg-gray-800/50">
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selected.has(a.id)}
                      onChange={() => toggleSelect(a.id)}
                    />
                  </td>
                  <td className="px-4 py-3">{a.vehicle_name}</td>
                  <td className="px-4 py-3"><TypeBadge type={a.alert_type || 'unknown'} /></td>
                  <td className="px-4 py-3">
                    {(a.escalation_level || 0) > 0
                      ? <span className="text-orange-400">Level {a.escalation_level}</span>
                      : <span className="text-gray-500">—</span>
                    }
                  </td>
                  <td className="px-4 py-3 text-gray-400">{a.created_at ? new Date(a.created_at).toLocaleString() : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <DataTable columns={historyColumns} data={alerts as unknown as Record<string, unknown>[]} searchKey="vehicle_name" />
      )}

      <p className="text-xs text-gray-500 mt-2">{alerts.length} alert{alerts.length !== 1 ? 's' : ''}</p>
    </div>
  );
}
