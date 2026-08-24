import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  RefreshCw, Loader2, AlertTriangle, RotateCcw, Clock,
} from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';
import DataGrid from '../../components/datagrid';
import type { AnyColumn } from '../../types';
import { Card } from '@/components/ui/card';

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

function formatTs(iso: string | null, tz?: string): string {
  return formatDate(iso, { timeZone: tz });
}

export default function ObjectStorageFileTable() {
  const { t } = useTranslation();
  const tz = useTimezone();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>('all');
  const [retrying, setRetrying] = useState<Set<number>>(new Set());
  const [bulkRetrying, setBulkRetrying] = useState(false);

  // Pull backend out of the shared health cache so the table can
  // render a backend-aware empty state without firing its own /health.
  const { data: health } = useQuery<HealthResponse>({
    queryKey: ['storage-health'],
    queryFn: () => apiJSON<HealthResponse>('/object-storage/health'),
    refetchInterval: POLL_MS,
    placeholderData: (prev) => prev,
  });

  // ``error`` and ``refetch`` were both dropped, and this table POLLS —
  // so the likely failure was a failed poll with rows already on screen.
  // It renders the upload QUEUE, which made the silence worse than
  // elsewhere: "no files pending upload" is the exact inverse of the
  // truth, printed on the surface an operator checks to confirm their
  // documents reached Drive.
  const { data, isLoading, error, refetch } = useQuery<FilesResponse>({
    queryKey: ['storage-files', filter],
    queryFn: () => apiJSON<FilesResponse>(`/object-storage/files?only=${filter}&limit=200`),
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
      await apiJSON(`/object-storage/files/${queueId}/retry`, { method: 'POST' });
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
        `/object-storage/files/retry-stuck`, { method: 'POST' },
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
    <Card padding="none">
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
                } min-h-tap`}
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
              className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-md border border-border hover:bg-muted disabled:opacity-50 min-h-tap"
            >
              {bulkRetrying ? <Loader2 className="animate-spin size-3" /> : <RotateCcw className="size-3" />}
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
          <Loader2 className="animate-spin size-3.5" />
          {t('common.loading')}
        </div>
      ) : (
        <DataGrid
          // The empty state is the GRID's to render, and routing it here
          // is what fixes the error: the hand-rolled branch above used to
          // short-circuit before the grid, so a failure had nowhere to
          // appear and came out as "no files".
          emptyMessage={isDiskBackend && filter === 'all'
            ? t('storage.files.empty_disk')
            : filter === 'stuck'
              ? t('storage.files.empty_stuck')
              : t('storage.files.empty')}
          error={error}
          onRetry={() => { void refetch(); }}
          columns={[
            {
              key: 'filename', label: t('storage.files.col_file'), sortable: true,
              render: (v) => (
                <span className="font-mono text-xs truncate max-w-[220px] inline-block" title={String(v)}>
                  {String(v)}
                </span>
              ),
            },
            {
              key: '_source', label: t('storage.files.col_source'), sortable: false,
              render: (_v, row) => {
                const r = row as unknown as StorageFile;
                return (
                  <span className="text-xs text-muted-foreground">
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
                  </span>
                );
              },
            },
            {
              key: 'file_size', label: t('storage.files.col_size'), sortable: true,
              render: (v) => (
                <span className="tabular-nums text-xs">{formatBytes(Number(v))}</span>
              ),
            },
            {
              key: 'is_stuck', label: t('storage.files.col_status'), sortable: true,
              render: (_v, row) => <StatusChip row={row as unknown as StorageFile} />,
            },
            {
              key: 'enqueued_at', label: t('storage.files.col_added'), sortable: true,
              render: (v) => (
                <span className="text-xs text-muted-foreground tabular-nums">
                  {formatTs(String(v), tz)}
                </span>
              ),
            },
            {
              key: '_actions', label: '', sortable: false,
              render: (_v, row) => {
                const r = row as unknown as StorageFile;
                return r.is_stuck ? (
                  <span className="inline-flex justify-end w-full">
                    <button
                      type="button"
                      onClick={() => retryOne(r.queue_id)}
                      disabled={retrying.has(r.queue_id)}
                      className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-border hover:bg-muted disabled:opacity-50 min-h-tap"
                    >
                      {retrying.has(r.queue_id)
                        ? <Loader2 className="animate-spin size-3" />
                        : <RefreshCw className="size-3" />}
                      {t('storage.files.retry')}
                    </button>
                  </span>
                ) : null;
              },
            },
          ] satisfies AnyColumn[]}
          data={items as unknown as Record<string, unknown>[]}
          enableToolbar={false}
          enablePagination={false}
        />
      )}
    </Card>
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
        <AlertTriangle className="size-3" />
        {label}
      </span>
    );
  }
  if ((row.attempts ?? 0) > 0) {
    return (
      <span className="inline-flex items-center gap-1 text-warn">
        <Clock className="size-3" />
        {t('storage.files.status_retrying', { n: row.attempts + 1 })}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-muted-foreground">
      <Clock className="size-3" />
      {t('storage.files.status_pending')}
    </span>
  );
}
