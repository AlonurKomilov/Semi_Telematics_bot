/**
 * In-app inbox feed (N5) — the persisted non-alert notices behind the
 * bell (team.* / system.*), written server-side by the intrinsic in_app
 * channel.  Alerts are NOT here: the bell reads those from
 * /alerts/pending (dual-source by design — alert ack/occurrence
 * semantics can't be honestly mirrored in an inbox row).
 *
 * ``useInbox(enabled)`` fetches one newest-first page + the unread count
 * while the dropdown is open.  ``useInboxUnread(enabled)`` is the
 * closed-bell badge companion: same query key, so an open panel keeps it
 * fresh for free.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiJSON } from '../../api/client';

export interface InboxNotice {
  id: number;
  category: string;
  source: string;         // namespace: 'team' | 'ai' | 'system' | future
  severity: 'info' | 'warning' | 'critical';
  title: string;
  body: string;
  url: string;
  created_at: string;
  /** The object chip ("Team", "AI") — '' = no chip. */
  context: string;
  /** Inline row action (expanded view) — server-validated relative url. */
  action: { label: string; url: string } | null;
  read: boolean;
}

export interface InboxResponse { notices: InboxNotice[]; unread: number }

const KEY = ['notifications', 'inbox'];
/** Shared cache key — the Notification center invalidates it after its own
 * mark-read writes so the bell stays in sync. */
export const INBOX_QUERY_KEY = KEY;

export function useInbox(enabled: boolean) {
  return useQuery({
    queryKey: KEY,
    enabled,
    staleTime: 30_000,
    queryFn: () => apiJSON<InboxResponse>('/notifications/inbox?limit=30'),
  });
}

/**
 * One SOURCE of the same inbox — for a feature page that keeps its own
 * bell (Applications) and must not invent a second notice store.  Its
 * own cache key, but under the same prefix, so a mark-read here
 * refreshes the top-bar bell in the same tick.
 *
 * The unread number is counted from the loaded page, like the bell's
 * per-source tab counts: the server's `unread` is the user's total
 * across every source and would overstate one feature's badge.
 */
export function useInboxSource(source: string, enabled: boolean) {
  const q = useQuery({
    queryKey: [...KEY, source],
    enabled: enabled && !!source,
    staleTime: 30_000,
    refetchInterval: enabled ? 120_000 : false,
    queryFn: () => apiJSON<InboxResponse>(
      `/notifications/inbox?limit=30&source=${encodeURIComponent(source)}`),
  });
  const notices = q.data?.notices ?? [];
  return { ...q, notices, unread: notices.filter((n) => !n.read).length };
}

/** Closed-bell unread badge — light poll, shares the cache with useInbox. */
export function useInboxUnread(enabled: boolean) {
  const { data } = useQuery({
    queryKey: KEY,
    enabled,
    staleTime: 60_000,
    refetchInterval: enabled ? 120_000 : false,
    queryFn: () => apiJSON<InboxResponse>('/notifications/inbox?limit=30'),
  });
  return data?.unread ?? 0;
}

export function useInboxActions() {
  const qc = useQueryClient();

  // setQueriesData, not setQueryData: the source-scoped feeds
  // (useInboxSource) are separate cache entries under the same prefix,
  // and a bell that patched only the unfiltered one would show no
  // feedback until the refetch landed.
  const patch = (fn: (n: InboxNotice) => InboxNotice, unread: (u: number) => number) =>
    qc.setQueriesData<InboxResponse>({ queryKey: KEY }, (cur) => cur && ({
      notices: cur.notices.map(fn),
      unread: Math.max(0, unread(cur.unread)),
    }));

  return {
    /**
     * Optimistic single mark-read; server confirms in the background.
     * Every writer here invalidates by PREFIX, so the source-scoped
     * feeds refresh too — one store has to mean one read-state,
     * whichever bell the click came from.
     */
    markRead: async (id: number) => {
      patch(n => (n.id === id && !n.read ? { ...n, read: true } : n), u => u - 1);
      try {
        await apiJSON('/notifications/inbox/read', { method: 'POST', body: { ids: [id] } });
      } catch {
        /* the resync below covers it */
      } finally {
        void qc.invalidateQueries({ queryKey: KEY });   // prefix match
      }
    },
    /**
     * Mark an explicit set read — what a SOURCE-scoped bell needs.
     * "Mark all read" there must not clear System and Activity too, and
     * read-all has no source parameter, so the caller names its own rows.
     */
    markManyRead: async (ids: number[]) => {
      if (!ids.length) return;
      const set = new Set(ids);
      patch(n => (set.has(n.id) && !n.read ? { ...n, read: true } : n),
            u => u - ids.length);
      try {
        await apiJSON('/notifications/inbox/read', { method: 'POST', body: { ids } });
      } catch {
        /* the resync below covers it */
      } finally {
        void qc.invalidateQueries({ queryKey: KEY });   // prefix match
      }
    },
    markAllRead: async () => {
      patch(n => ({ ...n, read: true }), () => 0);
      try {
        await apiJSON('/notifications/inbox/read-all', { method: 'POST' });
      } catch {
        /* the resync below covers it */
      } finally {
        void qc.invalidateQueries({ queryKey: KEY });   // prefix match
      }
    },
  };
}
