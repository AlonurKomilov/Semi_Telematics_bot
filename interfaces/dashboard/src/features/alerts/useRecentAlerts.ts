/**
 * Recent-alerts feed for the topbar bell dropdown — a lightweight glance,
 * NOT the full board.
 *
 * Deliberately separate from useAlertsQuery (which pulls the whole 2000-row
 * filter window for the DataGrid): the dropdown only ever shows a handful
 * of the newest un-acknowledged alerts, so it fetches a small page and is
 * ENABLED only while the popover is open — the closed bell adds zero
 * network cost (its badge count rides the shared useShellStats fetch).
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiJSON } from '../../api/client';
import type { AlertsResponse } from '../../types';

const RECENT_LIMIT = 12;

/**
 * Authoritative "which of these alerts are still active?" check — the
 * signal the live-banner watcher retires a sticky banner by.  Unlike the
 * capped recent feed, this is NOT windowed/paginated, so a long-running
 * critical never falsely reads as resolved.  Returns the active subset as
 * a Set of id-strings; on any error the caller keeps its banners (safe).
 */
export async function activeAmong(ids: (string | number)[]): Promise<Set<string>> {
  const list = ids.map(String).filter(Boolean);
  if (!list.length) return new Set();
  const r = await apiJSON<{ active_ids: string[] }>(
    `/alerts/active-among?ids=${encodeURIComponent(list.join(','))}`,
  );
  return new Set((r.active_ids ?? []).map(String));
}

export function useRecentAlerts(enabled: boolean, refetchInterval?: number) {
  return useQuery({
    queryKey: ['alerts', 'recent'],
    enabled,
    staleTime: 30_000,
    // The live-banner watcher passes a background poll interval; the bell
    // (no interval) shares the SAME cache entry, so one fetch serves both.
    refetchInterval: enabled ? (refetchInterval ?? false) : false,
    queryFn: () =>
      apiJSON<AlertsResponse>(
        `/alerts/pending?page_size=${RECENT_LIMIT}&days=7`,
      ),
  });
}

/** Acknowledge one or more alerts, then refresh the feed, the board cache,
 * and the shared stats the badge reads.
 *
 * ``keepalive`` is the staged-action tab-close hint: it lets the browser
 * finish the POST even though the page is unloading (a plain fetch can be
 * aborted mid-unload). The cache invalidation is skipped then — there is
 * no page left to repaint. */
export function useAckAlerts() {
  const qc = useQueryClient();
  return async (ids: (string | number)[], opts?: { keepalive?: boolean }) => {
    await apiJSON('/alerts/bulk-ack', {
      method: 'POST',
      body: { ids: ids.map((id) => Number(id)) },
      keepalive: opts?.keepalive,
    });
    if (opts?.keepalive) return;
    await Promise.all([
      qc.invalidateQueries({ queryKey: ['alerts'] }),
      qc.invalidateQueries({ queryKey: ['shell', 'overview-stats'] }),
    ]);
  };
}
