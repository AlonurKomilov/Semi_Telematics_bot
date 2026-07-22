/**
 * The bell-dropdown notification centre (docs/architecture/notifications.md
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
  Settings, RefreshCw, Check, CheckCheck, ArrowRight, BellOff,
  Wrench, HeartPulse, Fuel, MapPin, ShieldAlert, Camera, CircleParking,
  AlertTriangle, Loader2,
} from 'lucide-react';
import { toast } from 'sonner';
import type { Alert, AlertSeverity } from '../../types';
import type { Tone } from '../../lib/status';
import { toneText } from '../../lib/status';
import { formatAgoShort } from '../../utils/datetime';
import { stagedAction } from '../../components/banners';
import { useRecentAlerts, useAckAlerts } from './useRecentAlerts';
import { addStagedAcks, removeStagedAcks, useStagedAckIds } from './stagedAcks';

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

export function NotificationsPanel({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>('all');
  // Locally hide alerts the moment they're acked so the row doesn't linger
  // through the refetch round-trip.
  const [acked, setAcked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const { data, isLoading, isFetching, refetch } = useRecentAlerts(true);
  const ackAlerts = useAckAlerts();
  // Ids inside a pending "Acknowledge all" window — module-level store, so
  // the hide survives this panel unmounting when the dropdown closes (a
  // reopened panel must NOT resurface rows that are mid-countdown, and
  // must not allow a second overlapping stage of the same ids).
  const stagedIds = useStagedAckIds();

  const alerts = useMemo(
    () => (data?.alerts ?? []).filter(
      (a) => !acked.has(String(a.id)) && !stagedIds.has(String(a.id))),
    [data, acked, stagedIds],
  );
  const criticalCount = useMemo(
    () => alerts.filter((a) => a.severity === 'critical').length,
    [alerts],
  );
  const shown = tab === 'critical'
    ? alerts.filter((a) => a.severity === 'critical')
    : alerts;

  const goto = (path: string) => { onClose(); navigate(path); };

  const ack = async (ids: (string | number)[]) => {
    if (!ids.length || busy) return;
    const strIds = ids.map(String);
    setBusy(true);
    setAcked((prev) => new Set([...prev, ...strIds]));   // optimistic hide
    try {
      await ackAlerts(ids);
    } catch (e) {
      setAcked((prev) => {                                // roll back on failure
        const next = new Set(prev);
        strIds.forEach((id) => next.delete(id));
        return next;
      });
      toast.error(e instanceof Error ? e.message : 'Couldn’t acknowledge');
    } finally {
      setBusy(false);
    }
  };

  // Bulk ack is a STAGED action (the pending-action primitive's worked
  // example): acknowledging N alerts writes N accountability records
  // under this user's name, so it gets a cancel window — the request
  // fires only when the countdown ends.  Single-row acks stay instant
  // (one deliberate click on one row).  Hide-state lives in the
  // module-level stagedAcks store, NOT component state — this panel
  // unmounts when the dropdown closes, and the window must survive that.
  const ackAllStaged = (ids: (string | number)[]) => {
    if (!ids.length || busy) return;
    const strIds = ids.map(String);
    addStagedAcks(strIds);                               // hide during window
    stagedAction({
      label: `Acknowledging ${ids.length} alert${ids.length !== 1 ? 's' : ''}`,
      detail: 'Each acknowledgement is recorded under your name.',
      commit: async (hint) => {
        try {
          await ackAlerts(ids, hint);
        } finally {
          // Success: rows are truly acked (the refetch confirms).
          // Failure: rows REAPPEAR honestly while the banner offers Retry.
          removeStagedAcks(strIds);
        }
      },
      successMessage: `Acknowledged ${ids.length} alert${ids.length !== 1 ? 's' : ''}`,
      onCancel: () => removeStagedAcks(strIds),          // rows come back
    });
  };

  return (
    <div className="flex flex-col max-h-[32rem]">
      {/* Header — title + refresh + preferences gear (the ONE settings door) */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-border">
        {/* Titled "Alerts" (not "Notifications") so the object keeps ONE
            name across the bell, this panel, and the board it links to. */}
        <p className="text-base font-semibold">Alerts</p>
        <div className="flex items-center gap-0.5">
          <button
            onClick={() => refetch()}
            aria-label="Refresh"
            className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          >
            <RefreshCw size={14} className={isFetching ? 'animate-spin' : ''} aria-hidden />
          </button>
          <button
            onClick={() => goto('/alerts/preferences')}
            aria-label="Notification preferences"
            className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          >
            <Settings size={14} aria-hidden />
          </button>
        </div>
      </div>

      {/* Tabs — filter the glance (All / Critical) */}
      <div className="flex items-center gap-1 px-3 py-2 border-b border-border">
        <TabPill active={tab === 'all'} onClick={() => setTab('all')}>
          All{alerts.length ? ` ${alerts.length}` : ''}
        </TabPill>
        <TabPill active={tab === 'critical'} onClick={() => setTab('critical')}>
          Critical{criticalCount ? ` ${criticalCount}` : ''}
        </TabPill>
      </div>

      {/* Feed */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {isLoading ? (
          <div className="flex items-center justify-center py-10 text-muted-foreground">
            <Loader2 size={18} className="animate-spin" aria-hidden />
          </div>
        ) : shown.length === 0 ? (
          <EmptyState critical={tab === 'critical'} />
        ) : (
          <ul className="divide-y divide-border/60">
            {shown.map((a) => (
              <AlertRow key={String(a.id)} alert={a} onAck={() => ack([a.id])}
                        onOpen={() => goto('/alerts')} busy={busy} />
            ))}
          </ul>
        )}
      </div>

      {/* Footer — bulk ack + the board */}
      <div className="flex items-center justify-between px-3 py-2 border-t border-border">
        <button
          onClick={() => ackAllStaged(shown.map((a) => a.id))}
          disabled={busy || shown.length === 0}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:hover:text-muted-foreground transition-colors"
        >
          <CheckCheck size={14} aria-hidden /> Acknowledge all
        </button>
        <button
          onClick={() => goto('/alerts')}
          className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
        >
          Open Alerts <ArrowRight size={14} aria-hidden />
        </button>
      </div>
    </div>
  );
}

function TabPill({ active, onClick, children }: {
  active: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
        active
          ? 'bg-primary/15 text-primary'
          : 'text-muted-foreground hover:bg-muted hover:text-foreground'
      }`}
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
  const detail = alert.message || alert.last_detail || '';
  const age = formatAgoShort(alert.last_seen || alert.created_at);

  return (
    <li className="flex items-start gap-2.5 px-3 py-2.5 hover:bg-muted/50 transition-colors">
      <button onClick={onOpen} className="flex items-start gap-2.5 flex-1 min-w-0 text-left">
        <Icon size={16} className={`${toneText(tone)} mt-0.5 shrink-0`} aria-hidden />
        <span className="flex-1 min-w-0">
          <span className="flex items-baseline justify-between gap-2">
            <span className="text-sm font-medium truncate">{alert.vehicle_name || 'Vehicle'}</span>
            <span className="text-3xs text-muted-foreground shrink-0 tabular-nums">{age}</span>
          </span>
          {detail && (
            <span className="block text-xs text-muted-foreground truncate mt-0.5">{detail}</span>
          )}
        </span>
      </button>
      <button
        onClick={onAck}
        disabled={busy}
        aria-label="Acknowledge"
        // Always visible (dimmed) — NOT hover-only: this app runs on cab
        // tablets with no hover, where a reveal-on-hover action is
        // unreachable.  Solid on hover/focus.
        className="inline-flex size-6 items-center justify-center rounded-md text-muted-foreground/60 hover:bg-ok-bg hover:text-ok focus:text-ok transition-colors shrink-0 disabled:opacity-40"
      >
        <Check size={14} aria-hidden />
      </button>
    </li>
  );
}

function EmptyState({ critical }: { critical: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 px-4 text-center">
      <BellOff size={24} className="text-muted-foreground" aria-hidden />
      <p className="text-sm text-muted-foreground">
        {critical ? 'No critical alerts' : 'You’re all caught up'}
      </p>
    </div>
  );
}

export default NotificationsPanel;
