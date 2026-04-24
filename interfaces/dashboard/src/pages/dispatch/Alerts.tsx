import { useEffect, useState, useCallback } from 'react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
import type { Alert, AlertsResponse, BulkAckResponse } from '../../types';
import type { AnyColumn } from '../../types';

const ALERT_TYPES = ['all', 'fault', 'health', 'fuel', 'events', 'parking'] as const;

function TypeBadge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    fault: 'bg-orange-500/15 text-orange-700 dark:text-orange-400',
    health: 'bg-red-500/15 text-red-700 dark:text-red-400',
    fuel: 'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400',
    events: 'bg-purple-500/15 text-purple-700 dark:text-purple-400',
    parking: 'bg-cyan-500/15 text-cyan-700 dark:text-cyan-400',
  };
  const cls = colors[type] || 'bg-gray-500/20 text-muted-foreground';
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

  if (error && alerts.length === 0) return <p className="text-destructive">{error}</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Alerts</h1>
        <div className="flex items-center gap-3">
          {tab === 'pending' && selected.size > 0 && (
            <button
              onClick={ackSelected}
              disabled={acking}
              className="px-4 py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded-lg text-sm font-medium transition"
            >
              {acking ? 'Acknowledging...' : `Acknowledge (${selected.size})`}
            </button>
          )}
          <button
            onClick={fetchAlerts}
            className="text-sm text-muted-foreground hover:text-foreground transition"
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
              tab === t ? 'bg-muted/80 text-foreground' : 'text-muted-foreground hover:text-foreground'
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
                typeFilter === at ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:bg-muted/80'
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
          className="bg-muted border border-border rounded px-2.5 py-1 text-sm placeholder-muted-foreground focus:outline-none focus:border-ring w-48"
        />
        {tab === 'history' && (
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="bg-muted border border-border rounded px-2 py-1 text-sm text-foreground/80"
          >
            {[7, 14, 30, 60, 90].map((d) => (
              <option key={d} value={d}>{d} days</option>
            ))}
          </select>
        )}
      </div>

      {loading && alerts.length === 0 ? (
        <p className="text-muted-foreground">Loading...</p>
      ) : tab === 'pending' ? (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-card text-muted-foreground text-left">
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
                <th className="px-4 py-3">Time</th>
              </tr>
            </thead>
            <tbody>
              {alerts.length === 0 && (
                <tr><td colSpan={4} className="px-4 py-8 text-center text-muted-foreground">No pending alerts</td></tr>
              )}
              {alerts.map((a) => (
                <tr key={a.id} className="border-t border-border hover:bg-muted/50">
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selected.has(a.id)}
                      onChange={() => toggleSelect(a.id)}
                    />
                  </td>
                  <td className="px-4 py-3">{a.vehicle_name}</td>
                  <td className="px-4 py-3"><TypeBadge type={a.alert_type || 'unknown'} /></td>
                  <td className="px-4 py-3 text-muted-foreground">{a.created_at ? new Date(a.created_at).toLocaleString() : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <DataTable columns={historyColumns} data={alerts as unknown as Record<string, unknown>[]} searchKey="vehicle_name" />
      )}

      <p className="text-xs text-muted-foreground mt-2">{alerts.length} alert{alerts.length !== 1 ? 's' : ''}</p>
    </div>
  );
}
