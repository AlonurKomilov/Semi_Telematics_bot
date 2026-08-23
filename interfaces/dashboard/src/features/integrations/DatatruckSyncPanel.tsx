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
import { useTimezone } from '../../hooks/useTimezone';
import { apiJSON } from '../../api/client';
import { toast } from 'sonner';
import {
  getDatatruckSyncStatus, triggerDatatruckSync,
  startDatatruckPreview, getDatatruckPreview, applyDatatruckSync,
} from './api';
import type {
  DatatruckSyncStatusResponse,
  DatatruckPreviewStatus,
} from './types';
import { DATATRUCK_RESOURCE_LABELS, formatSyncTimestamp } from './labels';
import FeedsTable, { type FeedRow } from './FeedsTable';
import SyncPreviewModal from './SyncPreviewModal';
import DriverImportPanel from './DriverImportPanel';

type Feedback = { kind: 'success' | 'error' | 'info'; message: string } | null;

// Resources that accept a history window server-side.  Roster lists
// (drivers/trucks/trailers) always sync in full, so the window picker
// doesn't apply to them — we send ``days`` only for these.
const DATE_RANGED = new Set(['orders', 'work_orders']);

// Resources that MERGE into a user-owned SSOT (vehicle registry,
// work-orders module).  These go through the preview → accept flow so
// nothing is written until the operator approves the diff.  Roster-only
// (drivers) and the huge append-only orders feed sync directly.
const PREVIEWABLE = new Set(['trucks', 'trailers', 'work_orders']);

export default function DatatruckSyncPanel({
  windowDays,
  onToggle,
}: {
  windowDays: number;
  /** Enable/disable a resource's sync capability — folds the old
   *  per-resource checklist into the Synced-data rows. */
  onToggle: (capability: string, enabled: boolean) => void;
}) {
  const qc = useQueryClient();
  const tz = useTimezone();
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [triggering, setTriggering] = useState<Set<string>>(new Set());
  // The history window comes from the shared card-level picker (same
  // place on every integration) — applied to the date-ranged resources
  // (orders / work orders); roster syncs ignore it.
  // Open preview modal (plan → apply) + whether an apply is in flight.
  const [preview, setPreview] = useState<DatatruckPreviewStatus | null>(null);
  const [applying, setApplying] = useState(false);

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

  const busyOff = (resource: string) =>
    setTriggering((prev) => {
      const next = new Set(prev);
      next.delete(resource);
      return next;
    });

  // Poll a background preview until it's ready/failed.  The upstream
  // fetch is rate-gated — a 500-record work-orders window is 3+
  // MINUTES of pages — so there is deliberately NO fixed time ceiling:
  // we keep polling as long as the worker reports PROGRESS (fetched /
  // phase advancing), and only give up when the status stops moving
  // for ~2 minutes (a genuinely dead worker).  While it runs, the
  // feedback line narrates the progress so the spinner never reads as
  // stuck.
  const pollPreview = async (
    resource: string, previewId: string,
    lastSig = '', stalledPolls = 0,
  ) => {
    if (stalledPolls > 80) {  // ~2 min with NO progress at all
      setFeedback({ kind: 'error', message: `${resource}: preview stalled — try again` });
      busyOff(resource);
      return;
    }
    try {
      const st = await getDatatruckPreview(resource, previewId);
      if (st.state === 'ready') {
        setFeedback(null);
        setPreview(st);
        busyOff(resource);
      } else if (st.state === 'failed') {
        setFeedback({ kind: 'error', message: `${resource}: ${st.error || 'preview failed'}` });
        busyOff(resource);
      } else if (st.state === 'expired' || st.state === 'unavailable') {
        setFeedback({ kind: 'error', message: `${resource}: preview unavailable — try again` });
        busyOff(resource);
      } else {
        // queued / pending / running → narrate + keep polling while
        // anything is moving.
        const sig = `${st.state}|${st.phase ?? ''}|${st.fetched ?? 0}`;
        if (st.state === 'running') {
          setFeedback({
            kind: 'info',
            message: st.phase === 'planning'
              ? `${resource}: computing changes…`
              : `${resource}: preparing preview — ${st.fetched ?? 0}${
                  st.total_upstream ? ` of ${st.total_upstream}` : ''
                } records fetched…`,
          });
        }
        setTimeout(
          () => pollPreview(
            resource, previewId, sig,
            sig === lastSig ? stalledPolls + 1 : 0,
          ),
          1500,
        );
      }
    } catch (e) {
      setFeedback({ kind: 'error', message: `${resource}: ${e instanceof Error ? e.message : 'preview failed'}` });
      busyOff(resource);
    }
  };

  const handleSync = async (resource: string) => {
    if (triggering.has(resource)) return;
    setTriggering((prev) => new Set(prev).add(resource));
    setFeedback(null);
    const days = DATE_RANGED.has(resource) ? windowDays : undefined;

    if (PREVIEWABLE.has(resource)) {
      // Dry-run first (background) → poll → modal.  Nothing is written
      // until the operator accepts.  ``busyOff`` happens in pollPreview.
      try {
        const { preview_id } = await startDatatruckPreview(resource, days);
        pollPreview(resource, preview_id);
      } catch (e) {
        setFeedback({
          kind: 'error',
          message: `${resource}: ${e instanceof Error ? e.message : 'preview failed'}`,
        });
        busyOff(resource);
      }
      return;
    }

    // Roster-only / append-only — sync directly.
    try {
      await triggerDatatruckSync(resource, days);
      setFeedback({
        kind: 'info',
        message: days
          ? `${resource}: sync queued · last ${days} days`
          : `${resource}: sync queued`,
      });
      qc.invalidateQueries({ queryKey: ['datatruck-sync-status'] });
    } catch (e) {
      setFeedback({
        kind: 'error',
        message: `${resource}: ${e instanceof Error ? e.message : 'sync failed'}`,
      });
    } finally {
      busyOff(resource);
    }
  };

  const handleAccept = async () => {
    if (!preview || applying) return;
    setApplying(true);
    try {
      await applyDatatruckSync(preview.resource, preview.preview_id);
      setFeedback({ kind: 'info', message: `${preview.resource}: applying changes…` });
      qc.invalidateQueries({ queryKey: ['datatruck-sync-status'] });
      setPreview(null);
    } catch (e) {
      setFeedback({
        kind: 'error',
        message: `${preview.resource}: ${e instanceof Error ? e.message : 'apply failed'}`,
      });
    } finally {
      setApplying(false);
    }
  };

  const resources = data?.resources ?? {};

  // Map each Datatruck resource → the shared FeedRow shape.  All the
  // sync/preview/poll logic stays above; this just describes the row.
  const rows: FeedRow[] = DATATRUCK_RESOURCE_LABELS.map(([key, label]) => {
    const ov = resources[key];
    const enabled = ov?.enabled ?? false;
    const stored = ov?.stored;
    const run = ov?.sync;
    const live = run?.state === 'running' || run?.state === 'queued';
    const detailGap = (run?.detail_failed ?? 0) > 0;
    const freshness = stored && stored.count > 0
      ? `${stored.count.toLocaleString()} records · ${formatSyncTimestamp(stored.last_synced_at, tz) || 'sync stamp missing'}`
      : 'nothing synced yet';

    let badge: FeedRow['badge'] = null;
    if (live && run) {
      badge = {
        text: `page ${run.pages_done} · ${run.records_written.toLocaleString()} records`,
        tone: 'info',
        title: run.total_upstream != null
          ? `${run.records_written} of ${run.total_upstream.toLocaleString()} upstream records`
          : undefined,
      };
    } else if (run?.state === 'failed') {
      badge = { text: run.error || 'sync failed', tone: 'danger', title: run.error || undefined };
    }

    const note: FeedRow['note'] = (detailGap && !live)
      ? {
          text:
            `Synced the basics, but the detail endpoint was unreachable `
            + `(${run?.detail_failed} of ${(run?.detail_ok ?? 0) + (run?.detail_failed ?? 0)}) `
            + `— invoice #, payment, and vendor contact need an API token `
            + `scoped for work-order detail.`,
          title: run?.detail_error || undefined,
        }
      : null;

    const capability = ov?.capability;
    return {
      key, label, freshness, enabled, badge, note,
      feature: ov?.feature || undefined,
      toggle: capability
        ? { enabled, onChange: (en: boolean) => onToggle(capability, en) }
        : null,
      action: {
        label: 'Sync now',
        onClick: () => handleSync(key),
        busy: triggering.has(key) || live,
        disabled: triggering.has(key) || live || !enabled,
        title: !enabled
          ? 'Enable this resource’s toggle above first'
          : live
            ? 'A sync is already running for this resource'
            : `Pull ${label.toLowerCase()} from Datatruck now`,
      },
    };
  });

  return (
    <>
      <FeedsTable
        rows={rows}
        loading={isLoading}
        error={Boolean(error)}
        footer={feedback && (
          <p
            className={`px-3 py-1.5 text-2xs border-t border-border ${
              feedback.kind === 'error' ? 'text-danger' : 'text-muted-foreground'
            }`}
          >
            {feedback.message}
          </p>
        )}
      />
      <DriverImportPanel />
      <PayEstimateToggle />
      {preview && (
        <SyncPreviewModal
          resource={preview.resource}
          label={
            (DATATRUCK_RESOURCE_LABELS.find(([k]) => k === preview.resource)
              || [preview.resource, preview.resource])[1]
          }
          preview={preview}
          applying={applying}
          onAccept={handleAccept}
          onClose={() => { if (!applying) setPreview(null); }}
        />
      )}
    </>
  );
}


// ── Tariff-estimated driver pay (opt-in) ────────────────────────
//
// Synced loads carry no driver pay (settlements have no API), so gross
// reads as revenue.  This toggle estimates pay from each driver's
// payment tariff — OFF until the operator validates the math against a
// few real settlements in Datatruck's UI.

function PayEstimateToggle() {
  const qc = useQueryClient();
  const { data } = useQuery<{ enabled: boolean }>({
    queryKey: ['datatruck-pay-estimate'],
    queryFn: () => apiJSON<{ enabled: boolean }>('/integrations/datatruck/pay-estimate'),
  });
  const [busy, setBusy] = useState(false);
  const enabled = data?.enabled ?? false;

  const flip = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await apiJSON('/integrations/datatruck/pay-estimate', {
        method: 'PUT', body: { enabled: !enabled },
      });
      await qc.invalidateQueries({ queryKey: ['datatruck-pay-estimate'] });
      toast.success(!enabled
        ? 'Driver-pay estimates on — applies on the next orders sync'
        : 'Driver-pay estimates off');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not save the setting');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 flex items-start justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2.5">
      <div>
        <div className="text-sm font-medium text-foreground">
          Estimate driver pay from Datatruck tariffs
        </div>
        <p className="mt-0.5 text-2xs text-muted-foreground">
          Synced loads get driver pay computed from each driver&apos;s payment
          tariff (percentage of trip gross, or per-mile). An estimate, not the
          settlement — hand-entered pay always wins. Verify a few drivers
          against their real settlements before enabling.
        </p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        aria-label="Estimate driver pay from Datatruck tariffs"
        disabled={busy}
        onClick={() => { void flip(); }}
        className={`mt-0.5 shrink-0 relative inline-flex h-6 w-11 items-center rounded-full transition ${
          enabled ? 'bg-primary' : 'bg-muted-foreground/30'
        } ${busy ? 'opacity-60' : ''} min-h-tap`}
      >
        <span
          className={`inline-block h-5 w-5 transform rounded-full bg-background shadow transition ${
            enabled ? 'translate-x-5' : 'translate-x-0.5'
          }`}
        />
      </button>
    </div>
  );
}
