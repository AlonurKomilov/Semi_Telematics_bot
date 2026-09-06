/**
 * The bell-dropdown notification centre (capabilities/notifications/docs/ARCHITECTURE.md
 * §13a) — a quick recent-alerts glance without leaving the page.
 *
 * "Unread" here means un-acknowledged: the feed shows the newest pending
 * alerts, "Mark all read" acknowledges them, and the full board is one
 * click away.  That reuses the existing ack pipeline, so the glance needs
 * no separate read-state store.  The panel is the SINGLE gate for alerts +
 * preferences: rows triage inline, "Open Alerts" is the board, the gear is
 * Notification preferences.
 */
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Settings, RefreshCw, CheckCheck, ArrowRight, Bell, BellOff,
  Wrench, HeartPulse, Fuel, MapPin, ShieldAlert, Camera, CircleParking,
  AlertTriangle, Loader2, Users, Server, Bot, UserPlus, ChevronDown, ChevronUp,
} from 'lucide-react';
import { toast } from '../../lib/toast';
import { apiJSON } from '../../api/client';
import type { Alert, AlertSeverity } from '../../types';
import type { Tone } from '../../lib/status';
import { toneText } from '../../lib/status';
import { formatAgoShort } from '../../utils/datetime';
import { formatAlertDetailInline } from '../../utils/alertDescription';
import { Tip } from '../../components/tooltip';
import { useRecentAlerts } from './useRecentAlerts';
import { useStagedAckIds } from './stagedAcks';
import { useInbox, useInboxActions, type InboxNotice } from './useInbox';
import { ScrollRegion } from '../../components/scrolling';
import { cn } from '@/lib/utils';
import { SectionHeader } from '@/components/shell';

const SEVERITY_TONE: Record<AlertSeverity, Tone> = {
  critical: 'danger',
  warning: 'warn',
  info: 'info',
};

// alert_type → glyph.  Both singular + plural keys since the board rows
// use 'fault' while the preference columns use 'faults'.
const TYPE_ICON: Record<string, typeof Wrench> = {
  fault: Wrench, faults: Wrench,
  health: HeartPulse,
  fuel: Fuel,
  geofence: MapPin,
  event: ShieldAlert, events: ShieldAlert,
  camera: Camera,
  parking: CircleParking,
};

type Tab = 'all' | 'critical';
// Primary source tabs — the bell is a multi-source inbox: the alert
// glance (its own store, /alerts/pending) beside the persisted notices
// (team.*/ai.* → Activity, system.* → System) from /notifications/inbox.
// 'all' interleaves every source by time — the default landing view.
// Applications is permission-gated the way Alerts is: a bucket you can
// never receive shouldn't cost you a tab.
type Source = 'all' | 'alerts' | 'triggers' | 'applications' | 'activity' | 'system';

/** The category a fired personal trigger arrives under.  It lives in the
 *  ``alert`` namespace so alerting owns one inbox source — but it is not
 *  an Alerts-tab row: the Alerts tab is the account's shared queue, read
 *  from ``/alerts/pending``, and a trigger writes no board row at all.
 *  Filtering on the exact CATEGORY rather than the source is what keeps
 *  those two apart. */
const TRIGGER_CATEGORY = 'alert.trigger';

// One row of the merged All feed — an alert or a notice, time-sortable.
type MergedItem =
  | { kind: 'alert'; alert: Alert; ts: number }
  | { kind: 'notice'; notice: InboxNotice; ts: number };

export function NotificationsPanel(
  { onClose, canAlerts, canApplications }:
  { onClose: () => void; canAlerts: boolean; canApplications: boolean },
) {
  const navigate = useNavigate();
  const [src, setSrc] = useState<Source>('all');
  const [tab, setTab] = useState<Tab>('all');
  const [busy, setBusy] = useState(false);

  // A vehicle-less role (recruiter, HR) has no alerts access — the bell is
  // still their Notifications door; they just have no Alerts tab.  Don't
  // fetch a feed they can't read.
  const { data, isLoading, isFetching, refetch } = useRecentAlerts(canAlerts);

  // The persisted inbox (panel mounts only while the dropdown is open).
  const { data: inbox, isLoading: inboxLoading } = useInbox(true);
  const { markRead, markAllRead } = useInboxActions();
  const notices = useMemo(() => inbox?.notices ?? [], [inbox]);
  // 'system' namespace gets its own tab; 'applications' gets one only for
  // people who can act on them; every other source (team today, more
  // later) reads as personal account Activity.
  const systemNotices = useMemo(
    () => notices.filter((n) => n.source === 'system'), [notices]);
  const applicationNotices = useMemo(
    () => notices.filter((n) => n.source === 'applications'), [notices]);
  // A person's own fired triggers.  Their own bucket, never mixed into
  // Alerts: nobody else received these, nobody else can act on them, and
  // an Acknowledge verb over them would be acknowledging to yourself.
  const triggerNotices = useMemo(
    () => notices.filter((n) => n.category === TRIGGER_CATEGORY), [notices]);
  // Fail OPEN: without the Applications tab, application notices stay in
  // Activity rather than vanishing — a permission revoked after delivery
  // must not hide notices the person already holds.
  const activityNotices = useMemo(
    () => notices.filter((n) => n.source !== 'system'
      && n.category !== TRIGGER_CATEGORY
      && !(canApplications && n.source === 'applications')), [notices, canApplications]);
  // Per-tab counts come from the loaded page (newest 30) — a glance
  // number, deliberately approximate past that window.  The BELL badge
  // reads the server's true total (useInboxUnread), so nothing is lost.
  const unreadOf = (list: InboxNotice[]) => list.filter((n) => !n.read).length;
  // Force a valid tab if permissions shift under us.
  if (src === 'alerts' && !canAlerts) setSrc('all');
  if (src === 'applications' && !canApplications) setSrc('all');
  // The Triggers pill exists only while there are triggers to show, so
  // the last one ageing out of the page mid-session has to move the
  // person somewhere rather than leaving them on a tab that is gone.
  if (src === 'triggers' && triggerNotices.length === 0) setSrc('all');
  // Ids inside a pending "Acknowledge all" window — module-level store, so
  // the hide survives this panel unmounting when the dropdown closes (a
  // reopened panel must NOT resurface rows that are mid-countdown, and
  // must not allow a second overlapping stage of the same ids).
  const stagedIds = useStagedAckIds();

  // Claims don't hide rows (owned ≠ gone), so the only local hide left
  // is the staged store — rows mid-window from before the verb changed.
  const alerts = useMemo(
    () => (data?.alerts ?? []).filter(
      (a) => !stagedIds.has(String(a.id))),
    [data, stagedIds],
  );
  const criticalCount = useMemo(
    () => alerts.filter((a) => a.severity === 'critical').length,
    [alerts],
  );
  const shown = tab === 'critical'
    ? alerts.filter((a) => a.severity === 'critical')
    : alerts;

  // The glance total (matches the bell badge: pending alerts + server-true
  // unread notices) and how many sources actually contribute to it — the
  // All pill shows its number only when it isn't a restatement of one
  // source's own count.
  const allCount = (canAlerts ? alerts.length : 0) + (inbox?.unread ?? 0);
  const contributingSources =
    [(canAlerts ? alerts.length : 0) > 0,
     canApplications && applicationNotices.length > 0,
     triggerNotices.length > 0,
     activityNotices.length > 0,
     systemNotices.length > 0].filter(Boolean).length;

  // The All feed: alerts + notices interleaved newest-first.  Two stores,
  // one glance — exactly what the umbrella "Notifications" name promises.
  const merged = useMemo<MergedItem[]>(() => {
    const items: MergedItem[] = [
      ...(canAlerts ? alerts : []).map((a) => ({
        kind: 'alert' as const, alert: a,
        ts: Date.parse(a.last_seen || a.created_at || '') || 0,
      })),
      ...notices.map((n) => ({
        kind: 'notice' as const, notice: n,
        ts: Date.parse(n.created_at) || 0,
      })),
    ];
    // Criticals first, then newest — mirrors the Alerts tab's
    // severity-first server ordering so switching tabs doesn't reshuffle
    // the same items, and a critical is never buried under a fresher
    // warning just because it fired earlier.
    const rank = (m: MergedItem) =>
      (m.kind === 'alert' ? m.alert.severity : m.notice.severity) === 'critical' ? 0 : 1;
    return items.sort((x, y) => (rank(x) - rank(y)) || (y.ts - x.ts));
  }, [canAlerts, alerts, notices]);

  const goto = (path: string) => { onClose(); navigate(path); };

  const ack = async (ids: (string | number)[]) => {
    // A CLAIM now, not an acknowledge (owner decision 2026-08-30).  A
    // claimed alert is owned, not gone — so nothing is hidden and there
    // is nothing to optimistically roll back; the board's Working-on
    // column is where the claim shows.
    if (!ids.length || busy) return;
    setBusy(true);
    try {
      await apiJSON('/alerts/work', {
        method: 'POST',
        body: { ids: ids.map(Number).filter(Number.isFinite) },
      });
      toast.success('You’re on it — see My working on');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Couldn’t claim it');
    } finally {
      setBusy(false);
    }
  };

  // "Work on all" is a straight batch claim.  The staged countdown
  // existed because acknowledging was a one-way accountability write;
  // a claim is cheap, additive and honest to repeat, so the window
  // would be theatre.  The stagedAcks store stays only for rows mid-
  // window from before the verb changed.
  const ackAllStaged = (ids: (string | number)[]) => {
    // One-click batch-claiming a dozen alerts — criticals included —
    // silences their pagers with zero friction, and there is no undo
    // wide enough for a slip at that scale.  A count-confirm above a
    // handful, same threshold reasoning as the old bulk flow.
    if (ids.length >= 5 && !window.confirm(
      `Claim ${ids.length} alerts as yours?\n\nEach quiets its pager and `
      + 'lands in My working on.')) return;
    void ack(ids);
  };

  return (
    <div className="flex flex-col max-h-128">
      {/* Header — title + refresh + preferences gear (the ONE settings door) */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-border">
        {/* The bell is the Notifications door; this panel is its glance.
            Alerts are one source shown here — "Open Alerts" leads to the
            board, the gear leads to all notification preferences. */}
        <SectionHeader size="card">Notifications</SectionHeader>
        <div className="flex items-center gap-0.5">
          {canAlerts && (
            <button
              onClick={() => refetch()}
              aria-label="Refresh"
              className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors min-h-tap min-w-tap"
            >
              <RefreshCw className={cn(isFetching ? 'animate-spin' : '', 'size-3.5')} aria-hidden />
            </button>
          )}
          <button
            onClick={() => goto('/notifications/preferences')}
            aria-label="Notification preferences"
            className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors min-h-tap min-w-tap"
          >
            <Settings className="size-3.5" aria-hidden />
          </button>
        </div>
      </div>

      {/* Source tabs — All (merged, default) · Alerts (permission-gated) ·
          Applications (permission-gated) · Activity · System.  The All
          pill's inbox half is the SERVER's exact unread total (same field
          the bell badge reads); the alert half counts the loaded glance
          rows.  Per-source pills stay page-approximate.

          Wraps: someone holding BOTH gated tabs (an HR manager) has five
          pills, which exceed the w-80 popup — wrapping keeps every tab
          reachable instead of clipping the last one off the edge. */}
      <div className="flex flex-wrap items-center gap-1 px-3 py-2 border-b border-border">
        <TabPill active={src === 'all'} onClick={() => setSrc('all')} dim={allCount === 0}>
          {/* Show All's total ONLY when more than one source contributes —
              otherwise it just repeats that source's own count and reads as
              a duplicate tab ("All 12 · Alerts 12"). */}
          All{contributingSources > 1 && allCount ? ` ${allCount}` : ''}
        </TabPill>
        {canAlerts && (
          <TabPill active={src === 'alerts'} onClick={() => setSrc('alerts')}
                   dim={alerts.length === 0}>
            Alerts{alerts.length ? ` ${alerts.length}` : ''}
          </TabPill>
        )}
        {/* Only while there is something in it.  Every other pill names a
            bucket everyone receives; triggers are opt-in, and a permanent
            empty tab would charge the whole account for a feature most
            people never turn on. */}
        {triggerNotices.length > 0 && (
          <TabPill active={src === 'triggers'} onClick={() => setSrc('triggers')}
                   dim={unreadOf(triggerNotices) === 0}>
            Triggers{unreadOf(triggerNotices) ? ` ${unreadOf(triggerNotices)}` : ''}
          </TabPill>
        )}
        {canApplications && (
          <TabPill active={src === 'applications'} onClick={() => setSrc('applications')}
                   dim={applicationNotices.length === 0}>
            Applications{unreadOf(applicationNotices)
              ? ` ${unreadOf(applicationNotices)}` : ''}
          </TabPill>
        )}
        <TabPill active={src === 'activity'} onClick={() => setSrc('activity')}
                 dim={activityNotices.length === 0}>
          Activity{unreadOf(activityNotices) ? ` ${unreadOf(activityNotices)}` : ''}
        </TabPill>
        <TabPill active={src === 'system'} onClick={() => setSrc('system')}
                 dim={systemNotices.length === 0}>
          System{unreadOf(systemNotices) ? ` ${unreadOf(systemNotices)}` : ''}
        </TabPill>
      </div>

      {src === 'all' ? (
        <>
          {/* Merged feed — alert rows keep their ack; notice rows their
              read state.  Bulk verbs live on the dedicated tabs. */}
          <ScrollRegion label="Unread notifications" className="flex-1 min-h-0">
            {(isLoading || inboxLoading) && merged.length === 0 ? (
              <div className="flex items-center justify-center py-10 text-muted-foreground">
                <Loader2 className="animate-spin size-4.5" aria-hidden />
              </div>
            ) : merged.length === 0 ? (
              <EmptyState label="You’re all caught up" />
            ) : (
              <ul className="divide-y divide-border/60">
                {merged.map((m) => m.kind === 'alert' ? (
                  <AlertRow key={`a${String(m.alert.id)}`} alert={m.alert}
                            onAck={() => ack([m.alert.id])}
                            onOpen={() => goto(`/alerts?alertId=${m.alert.id}`)} busy={busy} />
                ) : (
                  <InboxRow key={`n${m.notice.id}`} notice={m.notice}
                            onOpen={() => {
                              if (!m.notice.read) void markRead(m.notice.id);
                              if (m.notice.url) goto(m.notice.url);
                            }}
                            onAction={(url) => {
                              if (!m.notice.read) void markRead(m.notice.id);
                              goto(url);
                            }} />
                ))}
              </ul>
            )}
          </ScrollRegion>
          <div className="flex items-center justify-between px-3 py-2 border-t border-border">
            <MarkAllReadButton
              unread={inbox?.unread ?? 0}
              onClick={() => void markAllRead()}
            />
            <button
              onClick={() => goto('/notifications')}
              className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline py-1 -my-1 min-h-tap"
            >
              See all <ArrowRight className="size-3.5" aria-hidden />
            </button>
          </div>
        </>
      ) : src === 'alerts' && canAlerts ? (
        <>
          {/* Severity sub-filter — alerts only */}
          <div className="flex items-center gap-1 px-3 py-2 border-b border-border">
            <TabPill active={tab === 'all'} onClick={() => setTab('all')}>
              All{alerts.length ? ` ${alerts.length}` : ''}
            </TabPill>
            <TabPill active={tab === 'critical'} onClick={() => setTab('critical')}>
              Critical{criticalCount ? ` ${criticalCount}` : ''}
            </TabPill>
          </div>

          {/* Feed */}
          <ScrollRegion label="All notifications" className="flex-1 min-h-0">
            {isLoading ? (
              <div className="flex items-center justify-center py-10 text-muted-foreground">
                <Loader2 className="animate-spin size-4.5" aria-hidden />
              </div>
            ) : shown.length === 0 ? (
              <EmptyState label={tab === 'critical' ? 'No critical alerts' : 'You’re all caught up'} />
            ) : (
              <ul className="divide-y divide-border/60">
                {shown.map((a) => (
                  <AlertRow key={String(a.id)} alert={a} onAck={() => ack([a.id])}
                            onOpen={() => goto(`/alerts?alertId=${a.id}`)} busy={busy} />
                ))}
              </ul>
            )}
          </ScrollRegion>

          {/* Footer — bulk ack + the board */}
          <div className="flex items-center justify-between px-3 py-2 border-t border-border">
            <button
              onClick={() => ackAllStaged(shown.map((a) => a.id))}
              disabled={busy || shown.length === 0}
              className="inline-flex items-center gap-1.5 py-1 -my-1 min-h-tap text-xs font-medium text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:hover:text-muted-foreground transition-colors py-1 -my-1 min-h-tap"
            >
              <Wrench className="size-3.5" aria-hidden /> Work on all
            </button>
            <button
              onClick={() => goto('/alerts')}
              className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline py-1 -my-1 min-h-tap"
            >
              Open Alerts <ArrowRight className="size-3.5" aria-hidden />
            </button>
          </div>
        </>
      ) : (
        /* Inbox tabs — persisted notices, read/unread */
        (() => {
          const list = src === 'system' ? systemNotices
            : src === 'applications' ? applicationNotices
            : src === 'triggers' ? triggerNotices
            : activityNotices;
          return (
            <>
              <ScrollRegion label="Archived notifications" className="flex-1 min-h-0">
                {inboxLoading ? (
                  <div className="flex items-center justify-center py-10 text-muted-foreground">
                    <Loader2 className="animate-spin size-4.5" aria-hidden />
                  </div>
                ) : list.length === 0 ? (
                  <EmptyState label={src === 'system'
                    ? 'No system notices'
                    : src === 'applications'
                    ? 'No new applications'
                    : src === 'triggers'
                    ? 'Nothing your triggers caught'
                    : 'No account activity yet'} />
                ) : (
                  <ul className="divide-y divide-border/60">
                    {list.map((n) => (
                      <InboxRow
                        key={n.id}
                        notice={n}
                        onOpen={() => {
                          if (!n.read) void markRead(n.id);
                          if (n.url) goto(n.url);
                        }}
                        onAction={(url) => {
                          if (!n.read) void markRead(n.id);
                          goto(url);
                        }}
                      />
                    ))}
                  </ul>
                )}
              </ScrollRegion>
              <div className="flex items-center justify-between px-3 py-2 border-t border-border">
                <MarkAllReadButton
                  unread={inbox?.unread ?? 0}
                  onClick={() => void markAllRead()}
                />
                {/* Triggers lead to the page that OWNS them — where the
                    same person sets them and reads the full history —
                    rather than to the generic notice archive. */}
                <button
                  onClick={() => goto(src === 'triggers' ? '/alerts/triggers' : '/notifications')}
                  className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline py-1 -my-1 min-h-tap"
                >
                  {src === 'triggers' ? 'My triggers' : 'See all'}
                  <ArrowRight className="size-3.5" aria-hidden />
                </button>
              </div>
            </>
          );
        })()
      )}
    </div>
  );
}

/** "Mark all read" — governs the INBOX notices (alerts are cleared with
 *  Acknowledge instead).  When there's nothing to mark it explains WHY it's
 *  disabled: a greyed verb above a populated list otherwise reads as broken
 *  (the All tab shows alerts + notices together). */
function MarkAllReadButton({ unread, onClick }: {
  unread: number; onClick: () => void;
}) {
  const btn = (
    <button
      onClick={onClick}
      disabled={unread === 0}
      className="inline-flex items-center gap-1.5 py-1 -my-1 min-h-tap text-xs font-medium text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:hover:text-muted-foreground transition-colors"
    >
      <CheckCheck className="size-3.5" aria-hidden /> Mark all read
    </button>
  );
  return unread === 0
    ? <Tip label="No unread notices — alerts resolve on the board, not here">
        <span>{btn}</span>
      </Tip>
    : btn;
}

function TabPill({ active, onClick, dim, children }: {
  active: boolean; onClick: () => void; dim?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      // `dim` = this source has nothing right now, so the tab reads empty
      // BEFORE it's clicked (no count needed, no wasted click).
      className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
        active
          ? 'bg-primary/15 text-foreground ring-1 ring-primary'
          : `hover:bg-muted hover:text-foreground ${
              dim ? 'text-muted-foreground/50' : 'text-muted-foreground'}`
      } min-h-tap`}
    >
      {children}
    </button>
  );
}

function AlertRow({ alert, onAck, onOpen, busy }: {
  alert: Alert; onAck: () => void; onOpen: () => void; busy: boolean;
}) {
  const tone = SEVERITY_TONE[alert.severity ?? 'info'] ?? 'info';
  const Icon = TYPE_ICON[alert.alert_type ?? ''] ?? AlertTriangle;
  // `last_detail` is the pipeline's DEDUP KEY ("parking:unknown:8h",
  // "fuel:8") — never display copy.  Route it through the shared
  // humanizer the board already uses so the glance reads in plain
  // language ("Parked in unverified location for 8 hours").
  const detail = formatAlertDetailInline(alert);
  const age = formatAgoShort(alert.last_seen || alert.created_at);
  // The row's time is last_seen — a chronic alert that keeps re-firing
  // reads "18m ago" even when it's been open for days.  Surface how long
  // it has ACTUALLY been open (first_seen) so ignoring it has a visible
  // cost; only past a day, where the two genuinely diverge.
  const openedMs = alert.created_at ? Date.parse(alert.created_at) : NaN;
  const lastMs = Date.parse(alert.last_seen || alert.created_at || '');
  const openDays = Number.isNaN(openedMs)
    ? 0 : Math.floor((Date.now() - openedMs) / 86_400_000);
  // Only when the row's age (the last FIRE) actually understates how long
  // the alert has been open — a single-fire alert already reads "2d ago",
  // so "open 2d" beside it would just repeat itself.
  const understatesAge = Number.isFinite(openedMs) && Number.isFinite(lastMs)
    && (lastMs - openedMs) > 3_600_000;
  const openFor = openDays >= 1 && understatesAge ? `open ${openDays}d` : '';

  return (
    <li className="flex items-start gap-2.5 px-3 py-2.5 hover:bg-muted/50 transition-colors">
      <button onClick={onOpen} className="flex items-start gap-2.5 flex-1 min-w-0 text-left py-0.5 -my-0.5 min-h-tap">
        <Icon className={`${toneText(tone)} mt-0.5 shrink-0 size-4`} aria-hidden />
        <span className="flex-1 min-w-0 py-0.5 -my-0.5 min-h-tap">
          <span className="flex items-baseline justify-between gap-2">
            <span className="min-w-0 inline-flex items-baseline gap-1.5">
              <span className="text-sm font-medium truncate">{alert.vehicle_name || 'Vehicle'}</span>
              {alert.company && (
                /* Which company this unit belongs to — only present on
                   multi-company accounts (server-tagged).  Matches the
                   company chip in maintenance/Tasks.tsx (same "disambiguate
                   a shared vehicle name" job → identical styling per the
                   design rules). */
                <span className="shrink-0 inline-flex items-center px-1.5 py-0.5 rounded bg-muted text-muted-foreground text-2xs">
                  {alert.company}
                </span>
              )}
              {openFor && (
                /* How long this alert has been OPEN (first_seen).  The age
                   on the right is the last FIRE — a chronic alert that keeps
                   re-firing reads "18m ago" and hides that it's days old. */
                <span className="shrink-0 inline-flex items-center px-1.5 py-0.5 rounded bg-muted text-muted-foreground text-2xs">
                  {openFor}
                </span>
              )}
            </span>
            {/* Ownership at the glance surface: the bell receives the
              claim data with every row, and hiding it made the second
              dispatcher's stand-down signal invisible exactly where
              quick triage happens. */}
          {(alert.working?.length ?? 0) > 0 && (
            <span className="inline-flex items-center gap-0.5 text-2xs text-warn shrink-0">
              <Wrench className="size-3" aria-hidden />
              {alert.working![0].name.split(' ')[0]}
              {alert.working!.length > 1 && ` +${alert.working!.length - 1}`}
            </span>
          )}
          <span className="text-2xs text-muted-foreground shrink-0 tabular-nums">{age}</span>
          </span>
          {detail && (
            <span className="block text-xs text-muted-foreground truncate mt-0.5">{detail}</span>
          )}
        </span>
      </button>
      {/* A CLAIM, not a resolution — the bell's quick action follows the
          board's verb (owner decision 2026-08-30).  Wrench, not check:
          the row is not being cleared, it is being taken. */}
      <button
        onClick={onAck}
        disabled={busy}
        aria-label="Work on it"
        // Always visible (dimmed) — NOT hover-only: this app runs on cab
        // tablets with no hover, where a reveal-on-hover action is
        // unreachable.  Solid on hover/focus.
        className="inline-flex size-6 items-center justify-center rounded-md text-muted-foreground/60 hover:bg-warn-bg hover:text-warn focus:text-warn transition-colors shrink-0 disabled:opacity-40 min-h-tap min-w-tap"
      >
        <Wrench className="size-3.5" aria-hidden />
      </button>
    </li>
  );
}

// Inbox source → glyph.  Kept coarse on purpose: the row's text carries
// the specifics; the icon just separates "people" / "AI" / "platform".
const SOURCE_ICON: Record<string, typeof Users> = {
  team: Users,
  ai: Bot,
  system: Server,
  // Same icon the Applications page wears, so one object keeps one face
  // wherever a driver application shows up.
  applications: UserPlus,
};

// Bodies longer than roughly one panel line earn the expand chevron.
const EXPAND_THRESHOLD = 76;

/** One inbox notice row — shared by the bell dropdown and the
 * Notification center so both surfaces speak one grammar.
 *
 * Expandable (chevron, always visible — no hover reveal on cab tablets)
 * when the body overflows a line OR the notice carries an inline action;
 * expanded shows the full body + the action button ("Review sessions",
 * "Open billing").  The action lives OUTSIDE the main row button —
 * nested buttons are invalid HTML. */
export function InboxRow({ notice, onOpen, onAction }: {
  notice: InboxNotice;
  onOpen: () => void;
  onAction: (url: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const tone = SEVERITY_TONE[notice.severity] ?? 'info';
  // Neutral fallback (Bell, not AlertTriangle) — this is the NON-alert
  // feed; an unknown future source shouldn't masquerade as an alert.
  const Icon = SOURCE_ICON[notice.source] ?? Bell;
  const age = formatAgoShort(notice.created_at);
  const expandable = !!notice.action || (notice.body?.length ?? 0) > EXPAND_THRESHOLD;
  return (
    <li className="px-3 py-2.5 hover:bg-muted/50 transition-colors">
      <div className="flex items-start gap-2.5">
        <button
          onClick={onOpen}
          className={`flex items-start gap-2.5 flex-1 min-w-0 text-left ${
            notice.read ? 'opacity-60' : ''
          }`}
        >
          <Icon className={`${toneText(tone)} mt-0.5 shrink-0 size-4`} aria-hidden />
          <span className="flex-1 min-w-0">
            <span className="flex items-baseline justify-between gap-2">
              <span className="text-sm font-medium truncate">{notice.title}</span>
              <span className="text-2xs text-muted-foreground shrink-0 tabular-nums">{age}</span>
            </span>
            {notice.body && (
              <span className={`block text-xs text-muted-foreground mt-0.5 ${
                expanded ? '' : 'truncate'
              }`}>
                {notice.body}
              </span>
            )}
            {notice.context && (
              /* The object chip — what the notice is ABOUT; especially
                 useful on the merged All feed where sources interleave. */
              <span className="inline-flex items-center rounded bg-muted px-1.5 py-px text-2xs font-medium text-muted-foreground mt-1">
                {notice.context}
              </span>
            )}
          </span>
        </button>
        {expandable && (
          <button
            onClick={() => setExpanded((e) => !e)}
            aria-label={expanded ? 'Collapse' : 'Expand'}
            aria-expanded={expanded}
            className="inline-flex size-6 items-center justify-center rounded-md text-muted-foreground/60 hover:bg-muted hover:text-foreground transition-colors shrink-0 min-h-tap min-w-tap"
          >
            {expanded
              ? <ChevronUp className="size-3.5" aria-hidden />
              : <ChevronDown className="size-3.5" aria-hidden />}
          </button>
        )}
        {!notice.read && (
          <span className="size-2 rounded-full bg-primary mt-1.5 shrink-0" aria-label="Unread" />
        )}
      </div>
      {expanded && notice.action && (
        <div className="mt-1.5 pl-6">
          <button
            onClick={() => onAction(notice.action!.url)}
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs font-medium text-foreground hover:bg-muted transition-colors min-h-tap"
          >
            {notice.action.label} <ArrowRight className="size-3" aria-hidden />
          </button>
        </div>
      )}
    </li>
  );
}

function EmptyState({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 px-4 text-center">
      <BellOff className="text-muted-foreground size-6" aria-hidden />
      <p className="text-sm text-muted-foreground">{label}</p>
    </div>
  );
}

export default NotificationsPanel;
