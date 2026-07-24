/**
 * Notification center — the browsable HISTORY behind the bell (N6).
 *
 * The dropdown is a glance (newest 30); this page walks the whole
 * retained window (60 days) with keyset pagination and a source filter.
 * Alerts intentionally live elsewhere (the Alerts board owns ack /
 * occurrence semantics) — this page is the inbox record: Activity + AI +
 * System notices, read/unread.
 *
 * Reached from the bell's "See all"; preferences stay one click away.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import {
  Bell, CheckCheck, Settings, ArrowRight, Loader2, BellOff,
  Users, Server, Bot,
} from 'lucide-react';
import { toast } from 'sonner';
import { apiJSON } from '@/api/client';
import { PageHeader } from '@/components/shell';
import type { Tone } from '../../lib/status';
import { toneText } from '../../lib/status';
import { formatAgoShort } from '../../utils/datetime';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import type { InboxNotice, InboxResponse } from './useInbox';
import { INBOX_QUERY_KEY } from './useInbox';

const PAGE = 30;

const SEVERITY_TONE: Record<string, Tone> = {
  critical: 'danger', warning: 'warn', info: 'info',
};
const SOURCE_ICON: Record<string, typeof Users> = {
  team: Users, ai: Bot, system: Server,
};

type Filter = '' | 'team' | 'ai' | 'system';
// Precise per-source filters (server filters by exact namespace).  The
// bell's coarse "Activity" bucket = team+ai; reusing that word here for
// team-only would collide, so the filter says "Team".
const FILTERS: { key: Filter; label: string }[] = [
  { key: '', label: 'All' },
  { key: 'team', label: 'Team' },
  { key: 'ai', label: 'AI' },
  { key: 'system', label: 'System' },
];

export default function NotificationCenter() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { hasAny } = useViewPermissions();
  const canAlerts = hasAny('can_alerts_all', 'can_alerts_vehicle');

  const [filter, setFilter] = useState<Filter>('');
  const [notices, setNotices] = useState<InboxNotice[]>([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);
  const [done, setDone] = useState(false);      // no older pages left
  // Monotonic request token: a response only applies if it's still the
  // NEWEST request — so a slow "Load older" can't append onto a list the
  // user has since re-filtered, and rapid filter switches can't let an
  // older replace-response clobber a newer one.
  const reqToken = useRef(0);

  const load = useCallback(async (src: Filter, before?: number) => {
    const token = ++reqToken.current;
    setLoading(true);
    try {
      const qs = new URLSearchParams({ limit: String(PAGE) });
      if (src) qs.set('source', src);
      if (before) qs.set('before_id', String(before));
      const r = await apiJSON<InboxResponse>(`/notifications/inbox?${qs}`);
      if (token !== reqToken.current) return;   // superseded — drop it
      setNotices((cur) => before ? [...cur, ...r.notices] : r.notices);
      setUnread(r.unread);
      setDone(r.notices.length < PAGE);
    } catch (e) {
      if (token !== reqToken.current) return;
      toast.error(e instanceof Error ? e.message : 'Couldn’t load notifications');
    } finally {
      if (token === reqToken.current) setLoading(false);
    }
  }, []);

  useEffect(() => { setNotices([]); setDone(false); void load(filter); }, [filter, load]);

  // Keep the bell's cached feed in sync after writes made from this page.
  const syncBell = () => void qc.invalidateQueries({ queryKey: INBOX_QUERY_KEY });

  const openNotice = async (n: InboxNotice) => {
    if (!n.read) {
      setNotices((cur) => cur.map((x) => x.id === n.id ? { ...x, read: true } : x));
      setUnread((u) => Math.max(0, u - 1));
      try {
        await apiJSON('/notifications/inbox/read', { method: 'POST', body: { ids: [n.id] } });
      } catch {
        // Resync honestly on failure — same contract as markAll.
        void load(filter);
      }
      syncBell();
    }
    if (n.url) navigate(n.url);
  };

  const markAll = async () => {
    setNotices((cur) => cur.map((x) => ({ ...x, read: true })));
    setUnread(0);
    try {
      await apiJSON('/notifications/inbox/read-all', { method: 'POST' });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Couldn’t mark all read');
      void load(filter);
    }
    syncBell();
  };

  return (
    <div>
      <PageHeader
        icon={Bell}
        title="Notification center"
        description="Everything that reached you — account activity, AI actions, and system notices, kept for 60 days."
      />

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="flex items-center gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.key || 'all'}
              onClick={() => setFilter(f.key)}
              disabled={loading}
              className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors disabled:opacity-60 ${
                filter === f.key
                  ? 'bg-primary/15 text-primary'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        <button
          onClick={() => void markAll()}
          disabled={unread === 0}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground disabled:opacity-40 transition-colors"
        >
          <CheckCheck size={14} aria-hidden /> Mark all read
        </button>
        <button
          onClick={() => navigate('/notifications/preferences')}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
        >
          <Settings size={14} aria-hidden /> Preferences
        </button>
      </div>

      {/* Alerts have their own home — say so instead of silently omitting. */}
      {canAlerts && (
        <p className="text-xs text-muted-foreground mb-3">
          Vehicle alerts live on the{' '}
          <button onClick={() => navigate('/alerts')} className="text-primary hover:underline font-medium">
            Alerts board <ArrowRight size={12} className="inline" aria-hidden />
          </button>{' '}
          with their acknowledgement history.
        </p>
      )}

      <section className="bg-card border border-border rounded-xl overflow-hidden">
        {loading && notices.length === 0 ? (
          <div className="flex items-center justify-center py-14 text-muted-foreground">
            <Loader2 size={18} className="animate-spin" aria-hidden />
          </div>
        ) : notices.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-14 px-4 text-center">
            <BellOff size={24} className="text-muted-foreground" aria-hidden />
            <p className="text-sm text-muted-foreground">
              Nothing here yet — notices appear as things happen.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-border/60">
            {notices.map((n) => {
              const Icon = SOURCE_ICON[n.source] ?? Bell;
              const tone = SEVERITY_TONE[n.severity] ?? 'info';
              return (
                <li key={n.id}>
                  <button
                    onClick={() => void openNotice(n)}
                    className={`flex items-start gap-3 w-full px-4 py-3 text-left transition-colors hover:bg-muted/50 ${
                      n.read ? 'opacity-60' : ''
                    }`}
                  >
                    <Icon size={16} className={`${toneText(tone)} mt-0.5 shrink-0`} aria-hidden />
                    <span className="flex-1 min-w-0">
                      <span className="flex items-baseline justify-between gap-3">
                        <span className="text-sm font-medium truncate">{n.title}</span>
                        <span className="text-2xs text-muted-foreground shrink-0 tabular-nums">
                          {formatAgoShort(n.created_at)}
                        </span>
                      </span>
                      {n.body && (
                        <span className="block text-xs text-muted-foreground mt-0.5">{n.body}</span>
                      )}
                      {n.context && (
                        <span className="inline-flex items-center rounded bg-muted px-1.5 py-px text-2xs font-medium text-muted-foreground mt-1.5">
                          {n.context}
                        </span>
                      )}
                    </span>
                    {!n.read && (
                      <span className="size-2 rounded-full bg-primary mt-1.5 shrink-0" aria-label="Unread" />
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        {!done && notices.length > 0 && (
          <div className="border-t border-border p-2 text-center">
            <button
              onClick={() => void load(filter, notices[notices.length - 1]?.id)}
              disabled={loading}
              className="text-xs font-medium text-primary hover:underline disabled:opacity-40"
            >
              {loading ? 'Loading…' : 'Load older'}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
