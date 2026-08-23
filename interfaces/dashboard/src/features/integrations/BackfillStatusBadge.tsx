/**
 * Inline backfill state badge rendered on the IntegrationCard.
 *
 * Polls every 5 seconds while a backfill is running; static once
 * the state transitions to ``completed``, ``failed``, or ``idle``.
 *
 * Adds a "Reset" affordance on ``failed`` so an operator can clear
 * a stuck record (e.g. heartbeat staleness coercion fired after an
 * API restart) and re-trigger immediately rather than waiting for
 * the 24h Redis TTL to expire.
 */

import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Loader2, RotateCcw } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { getBackfillStatus, resetBackfillStatus } from './api';
import { backfillProgressLabel } from './labels';
import type { BackfillStatus } from './types';

export default function BackfillStatusBadge({
  providerId,
  enabled = true,
}: {
  providerId: string;
  enabled?: boolean;
}) {
  const qc = useQueryClient();
  const [resetting, setResetting] = useState(false);
  const { data } = useQuery<BackfillStatus>({
    queryKey: ['integration-backfill-status', providerId],
    queryFn: () => getBackfillStatus(providerId),
    enabled,
    refetchInterval: (q) => {
      const s = q.state.data?.state;
      return s === 'running' || s === 'queued' ? 5_000 : false;
    },
    staleTime: 4_000,
  });

  // Detect the running/queued -> completed/failed transition.  When
  // a backfill completes, the backend stamps ``last_backfill_at`` on
  // the integration row — but the dashboard's ['integrations'] query
  // has no way to know that field changed and stays cached with the
  // old (empty) value, so the operator keeps seeing "Last history
  // backfill: never" forever even though the data IS fresh.
  // Invalidating ['integrations'] on the transition forces a refetch
  // so the "Last backfill: 2h ago" line and the smart Run-now label
  // pick up the new timestamp immediately.
  const prevStateRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    const prev = prevStateRef.current;
    const cur = data?.state;
    if (
      cur &&
      (cur === 'completed' || cur === 'failed') &&
      (prev === 'running' || prev === 'queued')
    ) {
      qc.invalidateQueries({ queryKey: ['integrations'] });
    }
    prevStateRef.current = cur;
  }, [data?.state, qc]);

  const handleReset = async () => {
    setResetting(true);
    try {
      await resetBackfillStatus(providerId);
      qc.invalidateQueries({ queryKey: ['integration-backfill-status', providerId] });
    } finally {
      setResetting(false);
    }
  };

  if (!data) return null;
  if (data.state === 'idle') return null;

  if (data.state === 'running') {
    return (
      <div className="flex items-center gap-1.5 text-2xs text-muted-foreground">
        <Loader2 className="animate-spin size-3" />
        <span>
          Backfilling {backfillProgressLabel(data)}
          {data.rows_inserted ? ` · ${data.rows_inserted.toLocaleString()} rows` : ''}
        </span>
      </div>
    );
  }

  if (data.state === 'completed') {
    return (
      <div className="flex items-center gap-1.5 text-2xs text-ok">
        <Check className="size-3" />
        Backfill done · {data.rows_inserted?.toLocaleString() ?? 0} rows
      </div>
    );
  }

  if (data.state === 'failed') {
    // Reset button surfaces here so an operator who sees the
    // heartbeat-staleness "task died mid-flight" outcome can clear
    // the badge + unblock the TOCTOU guard with one click.  The
    // staleness coercion in the backend already coerces stuck
    // ``running`` records to ``failed`` after 5 min, so this button
    // is the operator-visible recovery surface for that path.
    return (
      <div className="flex items-center gap-2 text-2xs text-danger">
        <span title={data.reason || undefined}>Backfill failed</span>
        <Button
          type="button"
          variant="outline"
          size="xs"
          onClick={handleReset}
          disabled={resetting}
          title="Clear the stuck record so you can re-run the backfill"
        >
          {resetting ? <Loader2 className="animate-spin" /> : <RotateCcw />}
          Reset
        </Button>
      </div>
    );
  }

  if (data.state === 'skipped') {
    // A 'skipped' badge often appears AFTER an in-flight backfill
    // got its progress clobbered by a duplicate-trigger race — the
    // operator's most useful escape is a one-click Reset that drops
    // the Redis record so the next Run-now click starts fresh.
    // ``reset_backfill_status`` refuses to clear ``completed`` so
    // accidentally clicking Reset on a fresh completion is safe.
    return (
      <div className="flex items-center gap-2 text-2xs text-muted-foreground">
        <span title={data.reason || undefined}>Skipped: {data.reason}</span>
        <Button
          type="button"
          variant="outline"
          size="xs"
          onClick={handleReset}
          disabled={resetting}
          title="Clear the skipped record so you can re-run the backfill"
        >
          {resetting ? <Loader2 className="animate-spin" /> : <RotateCcw />}
          Reset
        </Button>
      </div>
    );
  }

  return null;
}
