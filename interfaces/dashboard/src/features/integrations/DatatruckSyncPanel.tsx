/**
 * "Synced data" panel for TMS providers (Datatruck).
 *
 * One row per resource (drivers / trucks / trailers / loads / work
 * orders): the stored count + last-synced stamp from the datatruck_*
 * tables, the live run state from Redis, and a Sync-now button wired
 * to ``POST /integrations/datatruck/sync/{resource}``.
 *
 * Polls every 4s while any resource is queued/running so progress
 * ("page 4 · 38 records") ticks live, then settles back to
 * poll-on-mount-only.  Mirrors the ConnectedCompanies panel's
 * container shape so the Samsara and Datatruck cards read as the
 * same design family.
 */

import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Database, Loader2, RefreshCw } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { toneClasses } from '../../lib/status';
import { getDatatruckSyncStatus, triggerDatatruckSync } from './api';
import type {
  DatatruckResourceOverview,
  DatatruckSyncStatusResponse,
} from './types';
import { DATATRUCK_RESOURCE_LABELS, formatSyncTimestamp } from './labels';

type Feedback = { kind: 'success' | 'error' | 'info'; message: string } | null;

export default function DatatruckSyncPanel() {
  const qc = useQueryClient();
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [triggering, setTriggering] = useState<Set<string>>(new Set());

  const { data, isLoading, error } = useQuery<DatatruckSyncStatusResponse>({
    queryKey: ['datatruck-sync-status'],
    queryFn: getDatatruckSyncStatus,
    refetchInterval: (q) => {
      const resources = q.state.data?.resources ?? {};
      const anyLive = Object.values(resources).some(
        (r) => r.sync?.state === 'running' || r.sync?.state === 'queued',
      );
      return anyLive ? 4_000 : false;
    },
    staleTime: 3_000,
  });

  const handleSync = async (resource: string) => {
    if (triggering.has(resource)) return;
    setTriggering((prev) => new Set(prev).add(resource));
    setFeedback(null);
    try {
      await triggerDatatruckSync(resource);
      setFeedback({ kind: 'info', message: `${resource}: sync queued` });
      qc.invalidateQueries({ queryKey: ['datatruck-sync-status'] });
    } catch (e) {
      setFeedback({
        kind: 'error',
        message: `${resource}: ${e instanceof Error ? e.message : 'sync failed'}`,
      });
    } finally {
      setTriggering((prev) => {
        const next = new Set(prev);
        next.delete(resource);
        return next;
      });
    }
  };

  if (isLoading) {
    return (
      <div className="mb-4 border border-border rounded-md px-3 py-2 text-2xs text-muted-foreground flex items-center gap-1.5">
        <Loader2 size={12} className="animate-spin" />
        Loading sync status…
      </div>
    );
  }
  if (error) {
    return (
      <div className={`${toneClasses('danger')} mb-4 rounded px-2 py-1.5 text-2xs`}>
        Could not load sync status: {error instanceof Error ? error.message : 'error'}
      </div>
    );
  }

  const resources = data?.resources ?? {};

  return (
    <div className="mb-4 border border-border rounded-md">
      <div className="flex items-center justify-between px-3 py-2 bg-muted/40 border-b border-border">
        <span className="text-xs font-medium flex items-center gap-1.5">
          <Database size={12} />
          Synced data
        </span>
      </div>
      <ul className="divide-y divide-border">
        {DATATRUCK_RESOURCE_LABELS.map(([key, label]) => (
          <ResourceRow
            key={key}
            resource={key}
            label={label}
            overview={resources[key]}
            triggering={triggering.has(key)}
            onSync={() => handleSync(key)}
          />
        ))}
      </ul>
      {feedback && (
        <p
          className={`px-3 py-1.5 text-2xs border-t border-border ${
            feedback.kind === 'error' ? 'text-danger' : 'text-muted-foreground'
          }`}
        >
          {feedback.message}
        </p>
      )}
    </div>
  );
}

function ResourceRow({
  resource, label, overview, triggering, onSync,
}: {
  resource: string;
  label: string;
  overview: DatatruckResourceOverview | undefined;
  triggering: boolean;
  onSync: () => void;
}) {
  const enabled = overview?.enabled ?? false;
  const stored = overview?.stored;
  const run = overview?.sync;
  const live = run?.state === 'running' || run?.state === 'queued';

  // Stored summary: "60 records · synced 2 min ago", or the empty hint.
  const storedText = stored && stored.count > 0
    ? `${stored.count.toLocaleString()} records · ${formatSyncTimestamp(stored.last_synced_at) || 'sync stamp missing'}`
    : 'nothing synced yet';

  return (
    <li className={`px-3 py-2 text-xs ${enabled ? '' : 'opacity-60'}`}>
      <div className="flex items-center gap-3">
        <span className="font-medium w-28 shrink-0">{label}</span>
        <span className="flex-1 truncate text-muted-foreground text-2xs">
          {storedText}
        </span>
        {live && run && (
          <span
            className={`${toneClasses('info')} text-2xs px-1.5 py-0.5 rounded`}
            title={
              run.total_upstream != null
                ? `${run.records_written} of ${run.total_upstream.toLocaleString()} upstream records`
                : undefined
            }
          >
            page {run.pages_done} · {run.records_written.toLocaleString()} records
          </span>
        )}
        {!live && run?.state === 'failed' && (
          <span
            className={`${toneClasses('danger')} text-2xs px-1.5 py-0.5 rounded max-w-44 truncate`}
            title={run.error || undefined}
          >
            {run.error || 'sync failed'}
          </span>
        )}
        <Button
          type="button" variant="ghost" size="sm"
          onClick={onSync}
          disabled={triggering || live || !enabled}
          title={
            !enabled
              ? 'Enable this resource’s toggle above first'
              : live
                ? 'A sync is already running for this resource'
                : `Pull ${label.toLowerCase()} from Datatruck now`
          }
        >
          {(triggering || live)
            ? <Loader2 size={12} className="animate-spin" />
            : <RefreshCw size={12} />}
          Sync now
        </Button>
      </div>
    </li>
  );
}
