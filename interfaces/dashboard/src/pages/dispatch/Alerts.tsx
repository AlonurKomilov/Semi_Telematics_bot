import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, CheckCircle2, ChevronLeft, ChevronRight } from 'lucide-react';
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
  DateRangePresets,
} from '../../components/shell';
import type { Alert, AlertsResponse, BulkAckResponse } from '../../types';
import type { AnyColumn } from '../../types';
import { formatAlertDescription } from '../../utils/alertDescription';

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

// Severity dot + label.  Reads alert_history.severity (server-authoritative)
// so it always matches the bot's per-type formatter.  Falls back to 'warning'
// for legacy rows that haven't been migrated yet.
function SeverityDot({ severity }: { severity?: string }) {
  const sev = (severity === 'critical' || severity === 'info') ? severity : 'warning';
  const cfg: Record<string, { dot: string; text: string; label: string }> = {
    critical: { dot: 'bg-red-500',    text: 'text-red-600 dark:text-red-400',     label: 'Critical' },
    warning:  { dot: 'bg-orange-500', text: 'text-orange-600 dark:text-orange-400', label: 'Warning' },
    info:     { dot: 'bg-blue-500',   text: 'text-blue-600 dark:text-blue-400',   label: 'Info' },
  };
  const c = cfg[sev];
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${c.text}`}>
      <span className={`w-2 h-2 rounded-full ${c.dot}`} aria-hidden />
      {c.label}
    </span>
  );
}

function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max)}…` : s;
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
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>('pending');
  const [selected, setSelected] = useState<Set<string | number>>(new Set());
  const [typeFilter, setTypeFilter] = useState<AlertType>('all');
  const [severityFilter, setSeverityFilter] = useState<'all' | 'critical' | 'warning' | 'info'>('all');
  const [vehicleSearch, setVehicleSearch] = useState('');
  const [days, setDays] = useState(30);
  const [bulkError, setBulkError] = useState('');
  const [acking, setAcking] = useState(false);
  // Server-side pagination — see /alerts/pending {page, total_pages}.
  // 100/page is a comfortable scroll target; the API max is 500 if a
  // dispatcher prefers fewer round-trips.
  const PAGE_SIZE = 100;
  const [page, setPage] = useState(1);

  const queryKey = ['alerts', tab, typeFilter, vehicleSearch, page, tab === 'history' ? days : null] as const;
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
      // Server-side pagination — request a fixed page_size and the
      // current page number; bind Next/Prev to total_pages from the
      // API response so dispatchers can step through fleets that
      // have 300+ active logical alerts without overloading the table.
      params.set('page_size', String(PAGE_SIZE));
      params.set('page', String(page));
      const path = tab === 'pending' ? '/alerts/pending' : '/alerts/history';
      const qs = params.toString();
      return apiJSON<AlertsResponse>(`${path}${qs ? `?${qs}` : ''}`);
    },
    placeholderData: (prev) => prev,
  });
  const allAlerts: Alert[] = data?.alerts ?? [];
  // Apply severity filter client-side (server already sorts by
  // severity → last_seen so the bucket-by-severity grouping is stable).
  const alerts: Alert[] = severityFilter === 'all'
    ? allAlerts
    : allAlerts.filter((a) => (a.severity ?? 'warning') === severityFilter);
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
        title={t('alerts.page_title')}
        description={
          tab === 'pending'
            ? t('alerts.page_description_pending')
            : t('alerts.page_description_history')
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
                {acking ? t('alerts.acknowledging') : t('alerts.acknowledge_n', { n: selected.size })}
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
        {(['pending', 'history'] as const).map((tabId) => (
          <button
            key={tabId}
            onClick={() => { setTab(tabId); setPage(1); }}
            className={`px-4 py-2 text-sm font-medium transition border-b-2 -mb-px ${
              tab === tabId
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {tabId === 'pending' ? t('alerts.tab_pending') : t('alerts.tab_history')}
          </button>
        ))}
      </div>

      <FilterBar>
        <FilterChips
          options={ALERT_TYPES}
          value={typeFilter}
          onChange={(v) => { setTypeFilter(v); setPage(1); }}
        />
        {/* Severity filter — server-authoritative `alert_history.severity`.
            Drives client-side .filter() on the rendered alerts array; the
            counts are derived off the unfiltered list so each chip shows
            what it would surface. */}
        <FilterChips
          options={['all', 'critical', 'warning', 'info'] as const}
          value={severityFilter}
          onChange={(v) => { setSeverityFilter(v); setPage(1); }}
        />
        <input
          type="text"
          placeholder={t('alerts.vehicle_placeholder')}
          value={vehicleSearch}
          onChange={(e) => { setVehicleSearch(e.target.value); setPage(1); }}
          className="bg-background border border-border rounded-md px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:border-ring w-44"
        />
        {tab === 'history' && (
          <DateRangePresets
            value={days}
            onChange={(d) => { setDays(d); setPage(1); }}
            isFetching={isFetching}
          />
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
                <th className="px-4 py-3 w-24">Severity</th>
                <th className="px-4 py-3">Vehicle</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Description</th>
                <th className="px-4 py-3">Location</th>
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
                  <td className="px-4 py-3">
                    <SeverityDot severity={a.severity} />
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
                        title={t('alerts.total_occurrences')}
                      >
                        × {a.occurrence_count}
                      </span>
                    )}
                  </td>
                  {/* Description — friendly sentence rendered by the
                      shared formatter so dispatchers don't have to
                      decode ``parking:unsafe:8h`` / ``fuel:19`` / raw
                      event-IDs.  Raw ``last_detail`` stays available
                      on hover for support follow-ups. */}
                  <td
                    className="px-4 py-3 text-sm text-muted-foreground max-w-xs"
                    title={(a as Alert & { last_detail?: string }).last_detail || (a as Alert & { message?: string }).message || ''}
                  >
                    {truncate(
                      formatAlertDescription(a as Alert & { last_detail?: string; message?: string }),
                      80,
                    )}
                  </td>
                  {/* Location snapshot from alert_history.location.
                      Empty when the truck didn't have GPS at first fire. */}
                  <td
                    className="px-4 py-3 text-sm text-muted-foreground max-w-[14rem] truncate"
                    title={a.location || ''}
                  >
                    {a.location ? truncate(a.location, 30) : '—'}
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

      {/* Pagination footer — Next/Prev step through `alert_history` for
          fleets with hundreds of active alerts.  Severity filter applies
          *after* pagination on the client, so the displayed range is
          "what's on this page that also matched the severity chip". */}
      {data && data.count > 0 && (
        <div className="flex items-center justify-between mt-3">
          <p className="text-xs text-muted-foreground">
            {(() => {
              const total = data.count;
              const ps = data.page_size ?? PAGE_SIZE;
              const cur = data.page ?? page;
              const start = total === 0 ? 0 : (cur - 1) * ps + 1;
              const end = Math.min(cur * ps, total);
              const sevHidden = severityFilter !== 'all' && allAlerts.length !== alerts.length;
              return sevHidden ? (
                <>
                  Showing <strong>{alerts.length}</strong> of <strong>{allAlerts.length}</strong> on
                  this page · <strong>{total}</strong> total
                </>
              ) : (
                <>Showing <strong>{start}</strong>–<strong>{end}</strong> of <strong>{total}</strong> alerts</>
              );
            })()}
          </p>
          {(data.total_pages ?? 1) > 1 && (
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={(data.page ?? page) <= 1 || isFetching}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-border text-xs font-medium text-foreground/80 hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed transition"
              >
                <ChevronLeft size={14} />
                Prev
              </button>
              <span className="text-xs text-muted-foreground tabular-nums">
                Page <strong>{data.page ?? page}</strong> of <strong>{data.total_pages ?? 1}</strong>
              </span>
              <button
                onClick={() => setPage((p) => Math.min(data.total_pages ?? p, p + 1))}
                disabled={(data.page ?? page) >= (data.total_pages ?? 1) || isFetching}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-border text-xs font-medium text-foreground/80 hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed transition"
              >
                Next
                <ChevronRight size={14} />
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
