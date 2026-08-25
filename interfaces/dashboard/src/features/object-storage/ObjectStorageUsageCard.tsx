import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { HardDrive, Loader2, Trash2, AlertTriangle, RotateCw } from 'lucide-react';
import { toneClasses } from '../../lib/status';

import { apiJSON } from '../../api/client';
import DataGrid from '../../components/datagrid';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { InfoTip } from '../../components/tooltip';
import { Card } from '@/components/ui/card';

/**
 * What this account is actually STORING — the answer the page could not
 * give before.
 *
 * ``/object-storage/health`` reports ``quota.used_bytes`` as the pending-sync
 * footprint, and the file table lists sync-QUEUE rows.  Both are ~0 once
 * the queue drains, so an account with 793 files and 142 MB on disk saw
 * zeroes and an empty table — which reads as "storage is broken", not
 * "everything is synced".  This card reads the real walk.
 *
 * It also surfaces ORPHANS: files no database row references, left behind
 * when a parent record vanished without cleaning up.  Deleting them is
 * deliberately a two-step (review, then confirm) and never scheduled —
 * the scan that feeds it was wrong until recently, and a job that removes
 * customer files on a timer earns automation by track record.
 */

interface Bucket {
  kind: string;
  files: number;
  bytes: number;
  orphan_files: number;
  orphan_bytes: number;
}

interface UsageResponse {
  grace_days: number;
  total_files: number;
  total_bytes: number;
  orphan_files: number;
  orphan_bytes: number;
  buckets: Bucket[];
}

interface PurgeResponse {
  dry_run: boolean;
  candidate_count: number;
  candidate_bytes: number;
  deleted: number;
  deleted_bytes: number;
  sample: string[];
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

/** Folder name → what a human calls it. */
const KIND_LABEL: Record<string, string> = {
  'parking-maps':    'Parking snapshots',
  'camera-images':   'Dashcam stills',
  'applications':    'Driver applications',
  'work-orders':     'Work-order attachments',
  'inspections':     'Inspection media',
  'branding':        'Company branding',
  'company-banners': 'Company banners (legacy)',
  'company-logos':   'Company logos (legacy)',
  'knowledge':       'Knowledge base',
  'maintenance':     'Maintenance attachments',
  'driver-docs':     'Driver documents',
  'avatars':         'Avatars',
};

export default function ObjectStorageUsageCard() {
  const qc = useQueryClient();
  const [reviewing, setReviewing] = useState<PurgeResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // ``fetchError`` rather than reusing the ``error`` state above — that
  // one belongs to the purge mutation, and one name for two failures
  // means whichever wrote last is the only one the operator ever sees.
  const { data, isLoading, error: fetchError, refetch } = useQuery({
    queryKey: ['storage', 'usage'],
    queryFn: () => apiJSON<UsageResponse>('/object-storage/usage'),
  });

  // Step 1 of 2: a DRY RUN, which the server enforces by defaulting
  // confirm=false.  The user sees the count, the size and a sample of
  // real paths before anything is removed.
  async function review() {
    setError('');
    setBusy(true);
    try {
      setReviewing(await apiJSON<PurgeResponse>('/object-storage/orphans/purge', { method: 'POST' }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not scan for orphaned files');
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    setError('');
    setBusy(true);
    try {
      await apiJSON<PurgeResponse>('/object-storage/orphans/purge?confirm=true', { method: 'POST' });
      setReviewing(null);
      qc.invalidateQueries({ queryKey: ['storage'] });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) {
    return (
      <Card className="text-sm text-muted-foreground inline-flex items-center gap-2">
        <Loader2 className="animate-spin size-3.5" />
        Measuring stored files…
      </Card>
    );
  }
  // A failed fetch used to return null, taking the entire card — the
  // orphan-purge control included — off the page with no trace.  A
  // control that vanishes teaches nothing, and here it also removed the
  // only way to act on what the card is for.
  if (fetchError) {
    return (
      <div className={`rounded-lg border p-4 text-sm ${toneClasses('danger')}`}>
        <p className="font-medium">Could not measure stored files.</p>
        <p className="mt-1 text-xs opacity-90">
          {fetchError instanceof Error ? fetchError.message : String(fetchError)}
        </p>
        <button
          type="button"
          onClick={() => { void refetch(); }}
          className="mt-2 inline-flex items-center gap-1 text-xs font-medium underline underline-offset-2 hover:opacity-80"
        >
          <RotateCw className="size-3" aria-hidden="true" /> Retry
        </button>
      </div>
    );
  }
  if (!data) return null;

  const rows = data.buckets.map((b) => ({
    ...b,
    label: KIND_LABEL[b.kind] ?? b.kind,
  }));

  return (
    <Card className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <HardDrive className="text-muted-foreground size-4" />
        <h3 className="text-sm font-semibold">Stored files</h3>
        <InfoTip label="Everything this account holds on our servers, grouped by what created it. Files synced to your own Google Drive are not counted here — those live in your cloud." />
        <span className="ml-auto text-xs text-muted-foreground">
          {data.total_files.toLocaleString()} files · {formatBytes(data.total_bytes)}
        </span>
      </div>

      <DataGrid
        tableId="storage-usage"
        enableToolbar={false}
        enablePagination={false}
        columns={[
          { key: 'label', label: 'Kind', sortable: true },
          {
            key: 'files', label: 'Files', sortable: true, aggregable: true,
            render: (v) => (v as number).toLocaleString(),
          },
          {
            key: 'bytes', label: 'Size', sortable: true, aggregable: true,
            aggFormat: (v) => formatBytes(Number(v)),
            render: (v) => formatBytes(Number(v)),
          },
          {
            key: 'orphan_files', label: 'Unreferenced', sortable: true,
            // Zero is the healthy answer, so it stays muted rather than
            // reading as a metric worth chasing.
            render: (v, row) => {
              const n = Number(v ?? 0);
              if (!n) return <span className="text-muted-foreground">—</span>;
              return (
                <span className="text-warn">
                  {n.toLocaleString()} · {formatBytes(Number((row as Bucket).orphan_bytes))}
                </span>
              );
            },
          },
        ]}
        data={rows as unknown as Record<string, unknown>[]}
      />

      {data.orphan_files > 0 && (
        <div className="flex items-center gap-2 flex-wrap pt-1">
          <AlertTriangle className="text-warn shrink-0 size-3.5" />
          <span className="text-xs text-muted-foreground">
            {data.orphan_files.toLocaleString()} file
            {data.orphan_files === 1 ? '' : 's'} ({formatBytes(data.orphan_bytes)}) are
            not referenced by any record — usually left behind when the record that
            owned them was removed.
          </span>
          <button
            onClick={review}
            disabled={busy}
            className="ml-auto px-3 py-1.5 border border-border bg-background hover:bg-muted rounded-lg text-xs font-medium text-foreground transition disabled:opacity-50 min-h-tap"
          >
            Review…
          </button>
        </div>
      )}

      {error && <p className="text-xs text-destructive">{error}</p>}

      {/* Two-step by design: this dialog IS the dry run's result, so the
          user reads real paths before the destructive call is possible. */}
      <Dialog open={!!reviewing} onOpenChange={(o) => { if (!o) setReviewing(null); }}>
        <DialogContent size="xl">
          <DialogHeader>
            <DialogTitle>Delete unreferenced files?</DialogTitle>
            <DialogDescription>
              {reviewing && (
                <>
                  <span className="font-medium text-foreground">
                    {reviewing.candidate_count.toLocaleString()} files
                  </span>{' '}
                  ({formatBytes(reviewing.candidate_bytes)}) on our servers are not
                  referenced by any record. Files already synced to your Google Drive
                  are never touched. This cannot be undone.
                </>
              )}
            </DialogDescription>
          </DialogHeader>
          {reviewing && reviewing.sample.length > 0 && (
            <div className="max-h-48 overflow-y-auto rounded-lg border border-border">
              <ul className="divide-y divide-border">
                {reviewing.sample.map((p) => (
                  <li key={p} className="px-3 py-1.5 text-xs font-mono text-muted-foreground truncate">
                    {p}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <DialogFooter>
            <button
              onClick={() => setReviewing(null)}
              className="px-3 py-1.5 border border-border bg-background hover:bg-muted rounded-lg text-sm font-medium text-foreground transition"
            >
              Cancel
            </button>
            <button
              onClick={confirmDelete}
              disabled={busy}
              className="px-3 py-1.5 bg-destructive hover:bg-destructive/90 rounded-lg text-sm font-medium text-destructive-foreground transition inline-flex items-center gap-1.5 disabled:opacity-50 min-h-tap"
            >
              <Trash2 className="size-3.5" />
              Delete {reviewing?.candidate_count.toLocaleString()} files
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
