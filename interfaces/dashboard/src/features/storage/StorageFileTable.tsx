import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  RefreshCw, Loader2, AlertTriangle, RotateCcw, Clock,
} from 'lucide-react';
import { apiJSON } from '../../api/client';

/**
 * Storage file-manager — the "needs sync" queue.
 *
 * Synced files don't appear here by design; they live in Drive.  This
 * table is the disk → Drive outbox: pending rows + stuck rows + a
 * per-row Retry button + a bulk "Retry all stuck" action.
 *
 * Hidden entirely on the ``disk`` backend (no queue to drain).
 */

type Filter = 'all' | 'pending' | 'stuck';

interface StorageFile {
  queue_id: number;
  entity_type: string;
  entity_id: number | null;
  filename: string;
  file_size: number;
  media_type: string;
  content_type: string;
  inspection_id: number | null;
  vehicle_name: string | null;
  attempts: number;
  next_attempt_at: string | null;
  last_error: string | null;
  error_code: string | null;
  is_stuck: boolean;
  enqueued_at: string;
  updated_at: string;
}

interface FilesResponse {
  items: StorageFile[];
  count: number;
}

interface HealthResponse {
  backend: string;
}

const POLL_MS = 15_000;

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatTs(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString();
}

export default function StorageFileTable() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>('all');
  const [retrying, setRetrying] = useState<Set<number>>(new Set());
  const [bulkRetrying, setBulkRetrying] = useState(false);

  // Pull backend out of the shared health cache so the table can
  // render a backend-aware empty state without firing its own /health.
  const { data: health } = useQuery<HealthResponse>({
    queryKey: ['storage-health'],
    queryFn: () => apiJSON<HealthResponse>('/storage/health'),
    refetchInterval: POLL_MS,
    placeholderData: (prev) => prev,
  });

  const { data, isLoading } = useQuery<FilesResponse>({
    queryKey: ['storage-files', filter],
    queryFn: () => apiJSON<FilesResponse>(`/storage/files?only=${filter}&limit=200`),
    refetchInterval: POLL_MS,
    placeholderData: (prev) => prev,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['storage-files'] });
    qc.invalidateQueries({ queryKey: ['storage-health'] });
  };

  const retryOne = async (queueId: number) => {
    setRetrying(prev => new Set(prev).add(queueId));
    try {
      await apiJSON(`/storage/files/${queueId}/retry`, { method: 'POST' });
      toast.success(t('storage.files.retry_queued'));
      refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('storage.files.retry_failed'));
    } finally {
      setRetrying(prev => { const next = new Set(prev); next.delete(queueId); return next; });
    }
  };

  const retryAllStuck = async () => {
    setBulkRetrying(true);
    try {
      const res = await apiJSON<{ retried: number }>(
        `/storage/files/retry-stuck`, { method: 'POST' },
      );
      toast.success(t('storage.files.bulk_retry_done', { count: res.retried }));
      refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('storage.files.retry_failed'));
    } finally {
      setBulkRetrying(false);
    }
  };

  const items = data?.items ?? [];
  const stuckCount = items.filter(r => r.is_stuck).length;
  const isDiskBackend = health?.backend === 'disk';

  return (
    <div className="rounded-lg border border-border bg-card">
      {/* Header: title + filters + bulk action */}
      <div className="px-4 py-3 border-b border-border">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold">{t('storage.files.title')}</h3>
          <div className="inline-flex gap-1 ml-auto">
            {(['all', 'pending', 'stuck'] as const).map(k => (
              <button
                key={k}
                type="button"
                onClick={() => setFilter(k)}
                className={`px-2.5 py-1 text-xs rounded-md border transition ${
                  filter === k
                    ? 'bg-primary text-primary-foreground border-primary'
                    : 'bg-card hover:bg-muted border-border'
                }`}
              >
                {t(`storage.files.filter_${k}`)}
              </button>
            ))}
          </div>
          {filter === 'stuck' && stuckCount > 0 && (
            <button
              type="button"
              onClick={retryAllStuck}
              disabled={bulkRetrying}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-md border border-border hover:bg-muted disabled:opacity-50"
            >
              {bulkRetrying ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}
              {t('storage.files.retry_all_stuck', { count: stuckCount })}
            </button>
          )}
        </div>
        <p className="text-2xs text-muted-foreground mt-1">
          {t('storage.files.subtitle')}
        </p>
      </div>

      {/* Empty / loading / list */}
      {isLoading && !data ? (
        <div className="p-6 text-center text-sm text-muted-foreground inline-flex items-center gap-2 justify-center w-full">
          <Loader2 size={14} className="animate-spin" />
          {t('common.loading')}
        </div>
      ) : items.length === 0 ? (
        <div className="p-6 text-center text-sm text-muted-foreground">
          {isDiskBackend && filter === 'all'
            ? t('storage.files.empty_disk')
            : filter === 'stuck'
              ? t('storage.files.empty_stuck')
              : t('storage.files.empty')}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-muted-foreground bg-muted/30">
              <tr>
                <th className="text-left px-3 py-2 font-medium">{t('storage.files.col_file')}</th>
                <th className="text-left px-3 py-2 font-medium">{t('storage.files.col_source')}</th>
                <th className="text-right px-3 py-2 font-medium">{t('storage.files.col_size')}</th>
                <th className="text-left px-3 py-2 font-medium">{t('storage.files.col_status')}</th>
                <th className="text-left px-3 py-2 font-medium">{t('storage.files.col_added')}</th>
                <th className="text-right px-3 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {items.map(r => (
                <tr key={r.queue_id} className="border-t border-border hover:bg-muted/30">
                  <td className="px-3 py-2 font-mono text-xs truncate max-w-[220px]" title={r.filename}>
                    {r.filename}
                  </td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">
                    {r.vehicle_name
                      ? (
                        <>
                          {t('storage.files.source_vehicle', { name: r.vehicle_name })}
                          {r.inspection_id != null && (
                            <> · {t('storage.files.source_inspection', { id: r.inspection_id })}</>
                          )}
                        </>
                      )
                      : (r.entity_type ?? '—')}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-xs">
                    {formatBytes(r.file_size)}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    <StatusChip row={r} />
                  </td>
                  <td className="px-3 py-2 text-xs text-muted-foreground tabular-nums">
                    {formatTs(r.enqueued_at)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {r.is_stuck ? (
                      <button
                        type="button"
                        onClick={() => retryOne(r.queue_id)}
                        disabled={retrying.has(r.queue_id)}
                        className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-border hover:bg-muted disabled:opacity-50"
                      >
                        {retrying.has(r.queue_id)
                          ? <Loader2 size={12} className="animate-spin" />
                          : <RefreshCw size={12} />}
                        {t('storage.files.retry')}
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function StatusChip({ row }: { row: StorageFile }) {
  const { t } = useTranslation();
  if (row.is_stuck) {
    const label =
      row.error_code === 'token_expired' ? t('storage.files.status_drive_expired') :
      row.error_code === 'quota_exceeded' ? t('storage.files.status_drive_full') :
      row.error_code === 'forbidden' ? t('storage.files.status_forbidden') :
      t('storage.files.status_stuck');
    return (
      <span className="inline-flex items-center gap-1 text-danger" title={row.last_error ?? undefined}>
        <AlertTriangle size={12} />
        {label}
      </span>
    );
  }
  if ((row.attempts ?? 0) > 0) {
    return (
      <span className="inline-flex items-center gap-1 text-warn">
        <Clock size={12} />
        {t('storage.files.status_retrying', { n: row.attempts + 1 })}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-muted-foreground">
      <Clock size={12} />
      {t('storage.files.status_pending')}
    </span>
  );
}
