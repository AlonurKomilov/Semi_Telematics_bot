/**
 * Notification center — the browsable HISTORY behind the bell (N6).
 *
 * The dropdown is a glance (newest 30); this page walks the whole
 * retained window (60 days) with keyset pagination and a source filter.
 * Alerts intentionally live elsewhere (the Alerts board owns ack /
 * occurrence semantics) — this page is the inbox record: Activity
 * (team + AI) and System notices, read/unread.
 *
 * Reached from the bell's "See all"; preferences stay one click away.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import {
  Bell, CheckCheck, Settings, ArrowRight, Loader2, BellOff,
} from 'lucide-react';
import { toast } from 'sonner';
import { apiJSON } from '@/api/client';
import { PageHeader } from '@/components/shell';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { usePreference } from '../../preferences';
import type { InboxNotice, InboxResponse } from './useInbox';
import { INBOX_QUERY_KEY } from './useInbox';
import { InboxRow } from './NotificationsPanel';

const PAGE = 30;

// Filters name the same BUCKETS as the bell tabs and the preferences
// page ("Account activity" / "System") — one object, one name everywhere.
// Activity spans the team + ai namespaces (the server takes a bucket list);
// each row's context chip still says which source it came from, so nothing
// is lost by not filtering namespace-by-namespace.
type Filter = '' | 'applications' | 'team,ai' | 'system';
// Applications is PERMISSION-gated, not shown to everyone: a bucket a
// role can never receive is a tab that is always empty.  Same reasoning
// as the Alerts tab, which only appears for alert-capable roles.
const FILTERS: { key: Filter; label: string; perm?: string }[] = [
  { key: '', label: 'All' },
  { key: 'applications', label: 'Applications', perm: 'can_manage_applications' },
  { key: 'team,ai', label: 'Activity' },
  { key: 'system', label: 'System' },
];

export default function NotificationCenter() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { hasAny, ready: permsReady } = useViewPermissions();
  const canAlerts = hasAny('can_alerts_all', 'can_alerts_vehicle');

  // Remembered per user (server-backed, localStorage fast-paint) — a
  // dispatcher who lives in one bucket shouldn't re-pick it every visit.
  const { value: filterRaw, setValue: setFilterRaw } =
    usePreference('notifications.center.filter');
  const filterPref = filterRaw as Filter;
  const setFilter = setFilterRaw as (v: Filter) => void;
  // A permission-gated filter the viewer no longer holds reads as All.
  // Only once permissions have LOADED — "not loaded" is not "denied", and
  // a false negative here would silently discard their saved filter.
  const filterPerm = FILTERS.find((f) => f.key === filterPref)?.perm;
  const filter: Filter =
    filterPerm && permsReady && !hasAny(filterPerm) ? '' : filterPref;
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

  // NB: no setNotices([]) here.  The request token already guarantees only
  // the newest response applies, so keeping the current rows visible until
  // it lands avoids a blank flash — including the one-time correction when
  // useUserPreference's server value differs from the fast-paint cache.
  useEffect(() => { setDone(false); void load(filter); }, [filter, load]);

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
          {FILTERS.filter((f) => !f.perm || hasAny(f.perm)).map((f) => (
            <button
              key={f.key || 'all'}
              onClick={() => setFilter(f.key)}
              disabled={loading}
              className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors disabled:opacity-60 ${
                filter === f.key
                  ? 'bg-primary/15 text-primary'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              } min-h-tap`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        <button
          onClick={() => void markAll()}
          disabled={unread === 0}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground disabled:opacity-40 transition-colors py-1 -my-1 min-h-tap"
        >
          <CheckCheck className="size-3.5" aria-hidden /> Mark all read
        </button>
        <button
          onClick={() => navigate('/notifications/preferences')}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline py-1 -my-1 min-h-tap"
        >
          <Settings className="size-3.5" aria-hidden /> Preferences
        </button>
      </div>

      {/* Alerts have their own home — say so instead of silently omitting. */}
      {canAlerts && (
        <p className="text-xs text-muted-foreground mb-3">
          Vehicle alerts live on the{' '}
          <button onClick={() => navigate('/alerts')} className="text-primary hover:underline font-medium">
            Alerts board <ArrowRight className="inline size-3" aria-hidden />
          </button>{' '}
          with their acknowledgement history.
        </p>
      )}

      <section className="bg-card border border-border rounded-xl overflow-hidden">
        {loading && notices.length === 0 ? (
          <div className="flex items-center justify-center py-14 text-muted-foreground">
            <Loader2 className="animate-spin size-4.5" aria-hidden />
          </div>
        ) : notices.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-14 px-4 text-center">
            <BellOff className="text-muted-foreground size-6" aria-hidden />
            <p className="text-sm text-muted-foreground">
              Nothing here yet — notices appear as things happen.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-border/60">
            {notices.map((n) => (
              <InboxRow
                key={n.id}
                notice={n}
                onOpen={() => void openNotice(n)}
                onAction={(url) => {
                  if (!n.read) void openNotice({ ...n, url: '' });
                  navigate(url);
                }}
              />
            ))}
          </ul>
        )}
        {!done && notices.length > 0 && (
          <div className="border-t border-border p-2 text-center">
            <button
              onClick={() => void load(filter, notices[notices.length - 1]?.id)}
              disabled={loading}
              className="text-xs font-medium text-primary hover:underline disabled:opacity-40 py-1 -my-1 min-h-tap"
            >
              {loading ? 'Loading…' : 'Load older'}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
