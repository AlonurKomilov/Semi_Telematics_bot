import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, CheckCircle2 } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
  LastUpdated,
  FilterBar,
  FilterChips,
} from '../../components/shell';
import type { Alert, AlertsResponse, BulkAckResponse } from '../../types';
import type { AnyColumn } from '../../types';

const ALERT_TYPES = ['all', 'fault', 'health', 'fuel', 'events', 'parking'] as const;
type AlertType = typeof ALERT_TYPES[number];
type Tab = 'pending' | 'history';

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
    render: (v) => (v ? new Date(v as string).toLocaleString() : '—'),
  },
  {
    key: 'acknowledged_at',
    label: 'Acknowledged',
    render: (v) => (v ? new Date(v as string).toLocaleString() : '—'),
  },
];

export default function Alerts() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>('pending');
  const [selected, setSelected] = useState<Set<string | number>>(new Set());
  const [typeFilter, setTypeFilter] = useState<AlertType>('all');
  const [vehicleSearch, setVehicleSearch] = useState('');
  const [days, setDays] = useState(7);
  const [bulkError, setBulkError] = useState('');
  const [acking, setAcking] = useState(false);

  const queryKey = ['alerts', tab, typeFilter, vehicleSearch, tab === 'history' ? days : null] as const;
  const {
    data,
    isLoading: loading,
    isFetching,
    error: queryError,
    refetch,
    dataUpdatedAt,
  } = useQuery<AlertsResponse>({
    queryKey,
    queryFn: () => {
      const params = new URLSearchParams();
      if (typeFilter !== 'all') params.set('alert_type', typeFilter);
      if (vehicleSearch) params.set('vehicle', vehicleSearch);
      if (tab === 'history') params.set('days', String(days));
      const path = tab === 'pending' ? '/alerts/pending' : '/alerts/history';
      const qs = params.toString();
      return apiJSON<AlertsResponse>(`${path}${qs ? `?${qs}` : ''}`);
    },
    placeholderData: (prev) => prev,
  });
  const alerts: Alert[] = data?.alerts ?? [];
  const fetchError = queryError instanceof Error ? queryError.message : '';

  async function ackSelected() {
    if (selected.size === 0) return;
    setAcking(true);
    try {
      const ids = Array.from(selected).map(Number);
      await apiJSON<BulkAckResponse>('/alerts/bulk-ack', {
        method: 'POST',
        body: { ids },
      });
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ['alerts'] });
    } catch (e) {
      setBulkError(e instanceof Error ? e.message : 'Bulk acknowledge failed');
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

  return (
    <div>
      <PageHeader
        icon={Bell}
        title="Alerts"
        description={
          tab === 'pending'
            ? 'Notifications that still need acknowledgement. Tick rows to bulk-acknowledge.'
            : 'Past alerts and how they were resolved. Use filters to narrow down by vehicle or type.'
        }
        actions={
          <div className="flex items-center gap-3">
            {tab === 'pending' && selected.size > 0 && (
              <button
                onClick={ackSelected}
                disabled={acking}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded-md text-xs font-medium text-primary-foreground transition"
              >
                <CheckCircle2 size={13} />
                {acking ? 'Acknowledging…' : `Acknowledge (${selected.size})`}
              </button>
            )}
            <LastUpdated
              fetchedAt={dataUpdatedAt}
              isFetching={isFetching}
              onRefresh={refetch}
            />
          </div>
        }
      />

      <div className="flex gap-1 mb-4 border-b border-border">
        {(['pending', 'history'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium transition capitalize border-b-2 -mb-px ${
              tab === t
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      <FilterBar>
        <FilterChips options={ALERT_TYPES} value={typeFilter} onChange={setTypeFilter} />
        <input
          type="text"
          placeholder="Vehicle name…"
          value={vehicleSearch}
          onChange={(e) => setVehicleSearch(e.target.value)}
          className="bg-background border border-border rounded-md px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:border-ring w-44"
        />
        {tab === 'history' && (
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="bg-background border border-border rounded-md px-2 py-1.5 text-sm text-foreground/80"
          >
            {[7, 14, 30, 60, 90].map((d) => (
              <option key={d} value={d}>{d} days</option>
            ))}
          </select>
        )}
      </FilterBar>

      {bulkError && (
        <div className="mb-3">
          <ErrorState message={bulkError} />
        </div>
      )}

      {fetchError && alerts.length === 0 ? (
        <ErrorState
          title="Couldn't load alerts"
          message={fetchError}
          onRetry={() => refetch()}
        />
      ) : loading && alerts.length === 0 ? (
        <TableSkeleton rows={6} cols={5} />
      ) : alerts.length === 0 ? (
        <EmptyState
          icon={Bell}
          title={tab === 'pending' ? 'No pending alerts' : 'No alerts in this window'}
          description={
            tab === 'pending'
              ? 'You\'re all caught up — every alert has been acknowledged.'
              : 'Try widening the date range or removing filters.'
          }
        />
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
                <th className="px-4 py-3 w-20">Alert</th>
                <th className="px-4 py-3">Vehicle</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Time</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((a) => (
                <tr key={a.id} className="border-t border-border hover:bg-muted/50">
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selected.has(a.id)}
                      onChange={() => toggleSelect(a.id)}
                    />
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground font-mono">
                    #{a.id}
                  </td>
                  <td className="px-4 py-3">{a.vehicle_name}</td>
                  <td className="px-4 py-3">
                    <TypeBadge type={a.alert_type || 'unknown'} />
                    {/* Occurrence-count badge — "× 5" when this same
                        logical alert has fired multiple times without
                        being cleared.  Hidden for first-time alerts. */}
                    {(a.occurrence_count ?? 1) > 1 && (
                      <span
                        className="ml-2 inline-block px-2 py-0.5 rounded-full text-xs font-bold bg-orange-500/15 text-orange-500"
                        title="Total occurrences"
                      >
                        × {a.occurrence_count}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {a.last_seen
                      ? new Date(a.last_seen).toLocaleString()
                      : a.created_at ? new Date(a.created_at).toLocaleString() : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <DataTable
          columns={historyColumns}
          data={alerts as unknown as Record<string, unknown>[]}
          searchKey="vehicle_name"
        />
      )}

      {alerts.length > 0 && (
        <p className="text-xs text-muted-foreground mt-2">
          {alerts.length} alert{alerts.length !== 1 ? 's' : ''}
        </p>
      )}
    </div>
  );
}
