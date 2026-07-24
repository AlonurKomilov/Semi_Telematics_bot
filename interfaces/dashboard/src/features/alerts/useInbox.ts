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

  const patch = (fn: (n: InboxNotice) => InboxNotice, unread: (u: number) => number) =>
    qc.setQueryData<InboxResponse>(KEY, (cur) => cur && ({
      notices: cur.notices.map(fn),
      unread: Math.max(0, unread(cur.unread)),
    }));

  return {
    /** Optimistic single mark-read; server confirms in the background. */
    markRead: async (id: number) => {
      patch(n => (n.id === id && !n.read ? { ...n, read: true } : n), u => u - 1);
      try {
        await apiJSON('/notifications/inbox/read', { method: 'POST', body: { ids: [id] } });
      } catch {
        void qc.invalidateQueries({ queryKey: KEY });   // resync on failure
      }
    },
    markAllRead: async () => {
      patch(n => ({ ...n, read: true }), () => 0);
      try {
        await apiJSON('/notifications/inbox/read-all', { method: 'POST' });
      } catch {
        void qc.invalidateQueries({ queryKey: KEY });
      }
    },
  };
}
