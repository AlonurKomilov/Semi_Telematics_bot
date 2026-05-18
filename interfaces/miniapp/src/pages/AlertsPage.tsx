/**
 * AlertsPage — pending alerts list with optimistic ack + history view.
 *
 * Layout: 2-row alert cards (title + vehicle name + when, message),
 * full-width Done button with icon, swipe-friendly tap targets.
 * Empty state offers a quick jump to the last-N-days history.
 */

import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Icon24WarningTriangleOutline,
  Icon24FlashOutline,
  Icon24DropsOutline,
  Icon24LocationOutline,
  Icon24GearOutline,
  Icon24NotificationOutline,
  Icon24CameraOutline,
  Icon24SpeedometerMiddleOutline,
  Icon24ClockOutline,
  Icon24BroadcastOutline,
  Icon24DoneOutline,
  Icon24ChecksOutline,
  Icon24HistoryBackwardOutline,
  Icon24CheckCircleOutline,
  Icon24SearchSlashOutline,
  Icon24TruckOutline,
  Icon24LockOutline,
  Icon24GlobeOutline,
  Icon24MuteOutline,
  Icon24ChevronLeftOutline,
  Icon24ChevronRightOutline,
} from '@vkontakte/icons';
import { Placeholder } from '@telegram-apps/telegram-ui';
import { apiFetch, apiJSON, classifyError, type ClassifiedError } from '../api/client';
import type { Alert } from '../types';
import { Snackbar } from '../components/Snackbar';
import { BottomSheet } from '../components/BottomSheet';
import { RelativeTime } from '../components/RelativeTime';
import { ListRowsSkeleton } from '../components/Skeleton';
import { haptics } from '../hooks/useTelegram';
import { usePullToRefresh } from '../hooks/usePullToRefresh';
import { pluralize } from '../utils/units';

interface Props {
  active: boolean;
  /** Notify parent (App) of the new pending count so the tab badge stays in sync. */
  onCountChange?: (count: number) => void;
  /** Bumped by App when the badge poll detects a count change — triggers a list reload. */
  refreshKey?: number;
  /** Optional driver timezone for absolute fallbacks. */
  timezone?: string;
}

interface AlertHistoryItem {
  id: number;
  alert_type: string;
  vehicle_name: string;
  status: string;
  created_at: string;
  acknowledged_at: string | null;
}

// Single source of truth for type labels — used by filter chips AND title formatting.
const ALERT_TYPE_LABELS: Record<string, string> = {
  fault:       'Engine Fault',
  health:      'Vehicle Health',
  fuel:        'Fuel',
  events:      'Safety Event',
  geofence:    'Geofence',
  camera:      'Camera',
  parking:     'Parking',
  speed:       'Speeding',
  idle:        'Idling',
  maintenance: 'Maintenance',
  crash:       'Crash',
  rollover:    'Rollover',
};

const ICON_SIZE = 20;

function AlertIcon({ alert }: { alert: Alert }) {
  const k = `${alert.alert_type || ''} ${alert.alert_key || ''}`.toLowerCase();
  if (k.includes('crash') || k.includes('rollover'))                       return <Icon24BroadcastOutline width={ICON_SIZE} height={ICON_SIZE} />;
  if (k.includes('speed') || k.includes('overspeed'))                      return <Icon24SpeedometerMiddleOutline width={ICON_SIZE} height={ICON_SIZE} />;
  if (k.includes('fuel'))                                                   return <Icon24DropsOutline width={ICON_SIZE} height={ICON_SIZE} />;
  if (k.includes('fault') || k.includes('engine'))                         return <Icon24WarningTriangleOutline width={ICON_SIZE} height={ICON_SIZE} />;
  if (k.includes('idle'))                                                   return <Icon24ClockOutline width={ICON_SIZE} height={ICON_SIZE} />;
  if (k.includes('park') || k.includes('geofence') || k.includes('arrival') || k.includes('depart')) return <Icon24LocationOutline width={ICON_SIZE} height={ICON_SIZE} />;
  if (k.includes('maint'))                                                  return <Icon24GearOutline width={ICON_SIZE} height={ICON_SIZE} />;
  if (k.includes('camera'))                                                 return <Icon24CameraOutline width={ICON_SIZE} height={ICON_SIZE} />;
  if (k.includes('health'))                                                 return <Icon24FlashOutline width={ICON_SIZE} height={ICON_SIZE} />;
  return <Icon24NotificationOutline width={ICON_SIZE} height={ICON_SIZE} />;
}

function alertSeverity(a: Alert): 'critical' | 'warning' | 'info' {
  // SSOT: alert_history.severity from the server.  Frontends used to
  // re-derive from alert_type which disagreed with the bot's per-type
  // formatter — fixed by S2.
  if (a.severity === 'critical' || a.severity === 'warning' || a.severity === 'info') {
    return a.severity;
  }
  // Conservative fallback for legacy rows seeded before the severity
  // column landed (migration 032 backfills 'warning' so this branch
  // is only hit during the brief upgrade window).
  const t = a.alert_type?.toLowerCase() ?? '';
  if (t === 'crash' || t === 'rollover') return 'critical';
  if (t === 'fuel' || t === 'idle' || t === 'maintenance') return 'info';
  return 'warning';
}

const SEVERITY_LABEL: Record<'critical' | 'warning' | 'info', string> = {
  critical: 'Critical',
  warning:  'Warning',
  info:     'Info',
};

function alertTitle(a: Alert): string {
  const key = (a.alert_type || a.alert_key || 'Alert').toLowerCase();
  if (ALERT_TYPE_LABELS[key]) return ALERT_TYPE_LABELS[key];
  // Convert unknown snake_case backend keys to Title Case.
  const raw = a.alert_type || a.alert_key || 'Alert';
  return raw.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function formatAbsolute(iso: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(new Date(iso));
}

// ── Memoized alert card ────────────────────────────────────────────
// Extracted out of the AlertsPage render so React.memo can skip
// re-rendering unaffected cards when one card's expanded/absolute
// state flips.  With ~50–100 pending alerts this matters on low-end
// Android.  Callbacks are stable refs created via useCallback in the
// parent so the memo equality check actually holds.
interface AlertCardProps {
  alert: Alert;
  isExpanded: boolean;
  showAbsolute: boolean;
  isAcking: boolean;
  isMuting: boolean;
  isMuted: boolean;
  timezone?: string;
  onToggleAbsolute: (id: number) => void;
  onToggleExpanded: (id: number) => void;
  onAcknowledge: (id: number) => void;
  onMute: (id: number) => void;
}

const AlertCard = memo(function AlertCard({
  alert, isExpanded, showAbsolute, isAcking, isMuting, isMuted, timezone,
  onToggleAbsolute, onToggleExpanded, onAcknowledge, onMute,
}: AlertCardProps) {
  const sev = alertSeverity(alert);
  const hasLongMsg = (alert.message?.length ?? 0) > 80;
  return (
    <div className={`alert-card${sev === 'critical' ? ' alert-card--critical' : ''}`}>
      <div className={`alert-card__strip alert-card__strip--${sev}`} />
      <div className={`alert-card__icon-wrap alert-card__icon-wrap--${sev}`} aria-hidden>
        <AlertIcon alert={alert} />
      </div>
      <div className="alert-card__main">
        <div className="alert-card__row1">
          <span className="alert-card__title">
            {alertTitle(alert)}
            {/* Canonical AlertID — alert_history.id surfaced as #N so
                support / drivers can refer to a specific alert when
                discussing it ("ack #1234"). */}
            <span className="alert-card__id" title="Alert ID">#{alert.id}</span>
            {/* Occurrence-count badge — "× 5" means this same logical
                alert has fired 5 times without being cleared.  Hidden
                for first-time alerts to keep the card clean. */}
            {(alert.occurrence_count ?? 1) > 1 && (
              <span className="alert-card__count" title="Total occurrences">
                × {alert.occurrence_count}
              </span>
            )}
          </span>
          <span
            className="alert-card__when"
            onClick={() => onToggleAbsolute(alert.id)}
            title="Tap for exact time"
          >
            {showAbsolute
              ? formatAbsolute(alert.last_seen ?? alert.created_at)
              : <RelativeTime iso={alert.last_seen ?? alert.created_at} timezone={timezone} />}
          </span>
        </div>
        <div className="alert-card__meta">
          {/* Severity pill — server-authoritative.  Color-coded
              red/orange/blue with text label so colorblind users can
              still tell critical from warning. */}
          <span className={`alert-card__sev alert-card__sev--${sev}`}>
            {SEVERITY_LABEL[sev]}
          </span>
          {alert.vehicle_name && (
            <span className="alert-card__vehicle">
              <Icon24TruckOutline width={11} height={11} />
              {alert.vehicle_name}
            </span>
          )}
          {/* Location snapshot from alert_history.location.  Empty when
              the truck didn't have GPS at first-fire time. */}
          {alert.location && (
            <span className="alert-card__loc" title={alert.location}>
              <Icon24LocationOutline width={11} height={11} />
              {alert.location}
            </span>
          )}
        </div>
        {alert.message && (
          <div
            className={`alert-card__msg${isExpanded ? ' alert-card__msg--expanded' : ''}`}
            onClick={hasLongMsg ? () => onToggleExpanded(alert.id) : undefined}
            style={hasLongMsg ? { cursor: 'pointer' } : undefined}
          >
            {alert.message}
          </div>
        )}
      </div>
      <div className="alert-card__actions">
        {/* 🔇 Mute 7d — pause Telegram delivery without acknowledging.
            Useful for known-broken trucks ("we ordered the part, stop
            paging me about it for a week").  CRITICAL alerts ignore
            mutes server-side so this can't silence a real fire. */}
        <button
          className="alert-card__mute"
          disabled={isMuting || isMuted}
          onClick={() => onMute(alert.id)}
          aria-label={`Mute ${alertTitle(alert)} on ${alert.vehicle_name} for 7 days`}
          title="Mute for 7 days"
        >
          {isMuted
            ? '🔇'
            : isMuting
            ? '…'
            : <Icon24MuteOutline width={14} height={14} />}
        </button>
        <button
          className="alert-card__ack"
          disabled={isAcking}
          onClick={() => onAcknowledge(alert.id)}
          aria-label={`Acknowledge ${alertTitle(alert)} on ${alert.vehicle_name}`}
        >
          {isAcking ? '…' : <><Icon24DoneOutline width={16} height={16} />Done</>}
        </button>
      </div>
    </div>
  );
});

export function AlertsPage({ active, onCountChange, refreshKey, timezone }: Props) {
  const { t } = useTranslation();
  const [alerts, setAlerts]           = useState<Alert[]>([]);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState<ClassifiedError | null>(null);
  const [acking, setAcking]           = useState<Set<number>>(new Set());
  const [muting, setMuting]           = useState<Set<number>>(new Set());
  const [mutedIds, setMutedIds]       = useState<Set<number>>(new Set());
  const [bulkAcking, setBulkAcking]   = useState(false);
  // Server-reported total of un-filtered active alerts.  When > alerts.length
  // we either hit the page_size=500 cap or a truncation happened — show a
  // "Showing X of Y" hint so the dispatcher knows.
  const [totalCount, setTotalCount]   = useState(0);
  const [snackOpen, setSnackOpen]     = useState(false);
  const [snackText, setSnackText]     = useState('');
  const [snackKind, setSnackKind]     = useState<'success' | 'error' | 'info'>('info');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory]         = useState<AlertHistoryItem[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyDays, setHistoryDays] = useState(7);
  const [typeFilter, setTypeFilter]   = useState<string>('all');
  const [severityFilter, setSeverityFilter] = useState<'all' | 'critical' | 'warning' | 'info'>('all');
  const [vehicleFilter, setVehicleFilter] = useState<string>('all');
  // Client-side pagination — the load() above already pulls everything
  // (page_size=500) and all three filters operate client-side, so paging
  // server-side here would let a severity chip drop to "0 results" on
  // page N while later pages have the rows.  Slicing the *post-filter*
  // list on the client keeps the chips coherent.
  const PAGE_SIZE = 50;
  const [page, setPage] = useState(1);
  // Per-card expanded message & absolute timestamp toggles.
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [absoluteIds, setAbsoluteIds] = useState<Set<number>>(new Set());

  // Ack-all confirmation.  Bulk-ack is destructive (no per-row undo) so
  // a misplaced thumb on the header should not wipe every alert.
  const [confirmBulkAck, setConfirmBulkAck] = useState(false);

  function toast(text: string, kind: 'success' | 'error' | 'info' = 'info') {
    setSnackText(text); setSnackKind(kind); setSnackOpen(true);
  }

  const load = useCallback(async () => {
    setError(null);
    try {
      // page_size=500 — fleets with hundreds of active alerts would
      // otherwise hit the API's default 50-row cap and the badge
      // count + "Ack all" button would silently lose alerts.
      const data = await apiJSON<{ alerts: Alert[]; count?: number }>(
        '/api/alerts/pending?page_size=500',
      );
      // Trust server order (severity → last_seen DESC).
      const arr = data.alerts ?? [];
      setAlerts(arr);
      // count comes from the server's pre-pagination total; fall back
      // to arr.length so the badge stays accurate when count is absent.
      const total = typeof data.count === 'number' ? data.count : arr.length;
      setTotalCount(total);
      onCountChange?.(total);
    } catch (e) {
      console.error('Failed to load alerts:', e);
      setError(classifyError(e));
    }
  }, [onCountChange]);

  useEffect(() => {
    setLoading(true);
    load().finally(() => setLoading(false));
  }, [load]);

  useEffect(() => { if (active) load(); }, [active, load]);

  // Reset to page 1 whenever the filter set changes — otherwise switching
  // from "all" to "critical" while on page 4 lands on an empty page.
  useEffect(() => { setPage(1); }, [typeFilter, vehicleFilter, severityFilter]);

  useEffect(() => {
    if (refreshKey !== undefined && refreshKey > 0) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  const ptr = usePullToRefresh<HTMLDivElement>({ onRefresh: load });

  // ── History fetch ─────────────────────────────────────────────────

  const fetchHistory = useCallback(async (days: number) => {
    setHistoryLoading(true);
    setHistory(null);
    try {
      const data = await apiJSON<{ alerts: AlertHistoryItem[] }>(`/api/alerts/history?days=${days}&page_size=100`);
      setHistory(data.alerts ?? []);
    } catch (e) {
      console.error('Failed to load history:', e);
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const openHistory = useCallback(() => {
    setHistoryOpen(true);
    fetchHistory(historyDays);
  }, [historyDays, fetchHistory]);

  // ── Bulk ack ──────────────────────────────────────────────────────

  async function bulkAck() {
    const ids = alerts.map(a => a.id);
    if (ids.length === 0) return;
    setBulkAcking(true);
    const snapshot = [...alerts];
    setAlerts([]);
    onCountChange?.(0);
    try {
      await apiFetch('/api/alerts/bulk-ack', { method: 'POST', body: { ids } });
      haptics.success();
      toast(`Acknowledged all ${pluralize(ids.length, 'alert')}`, 'success');
    } catch (e) {
      console.error('Bulk ack failed:', e);
      setAlerts(snapshot);
      onCountChange?.(snapshot.length);
      haptics.error();
      toast(t('toasts.ack_failed'), 'error');
    } finally {
      setBulkAcking(false);
    }
  }

  // useCallback so the AlertCard memo's onAcknowledge prop is stable.
  // Snapshot is captured via the functional setAlerts updater so we
  // don't have to read `alerts` from the closure (which would otherwise
  // make the dep list change on every ack).
  const acknowledge = useCallback(async (id: number) => {
    setAcking(prev => new Set(prev).add(id));
    let snapshot: Alert | undefined;
    setAlerts(prev => {
      snapshot = prev.find(a => a.id === id);
      const next = prev.filter(a => a.id !== id);
      onCountChange?.(next.length);
      return next;
    });
    try {
      await apiFetch(`/api/alerts/${id}/acknowledge`, { method: 'POST' });
      haptics.success();
      toast(t('toasts.ack_success'), 'success');
    } catch (e) {
      console.error('Failed to acknowledge alert:', e);
      if (snapshot) {
        const restored = snapshot;
        setAlerts(prev => {
          const next = [restored, ...prev.filter(a => a.id !== id)];
          onCountChange?.(next.length);
          return next;
        });
      }
      haptics.error();
      toast(t('toasts.ack_failed'), 'error');
    } finally {
      setAcking(prev => { const next = new Set(prev); next.delete(id); return next; });
    }
  }, [onCountChange, t]);

  // Mute the alert for 7 days — pauses Telegram delivery without acking.
  // The card stays in the list (you still see it muted with a 🔇 marker)
  // and reappears as fully active once the mute window expires.
  const muteAlert = useCallback(async (id: number) => {
    if (mutedIds.has(id)) return;
    setMuting(prev => new Set(prev).add(id));
    try {
      await apiFetch(`/api/alerts/${id}/mute`, {
        method: 'POST',
        body: { hours: 24 * 7 },
      });
      setMutedIds(prev => new Set(prev).add(id));
      haptics.success();
      toast(t('toasts.mute_7d'), 'success');
    } catch (e) {
      console.error('Failed to mute alert:', e);
      haptics.error();
      toast(t('toasts.mute_failed'), 'error');
    } finally {
      setMuting(prev => { const next = new Set(prev); next.delete(id); return next; });
    }
  }, [mutedIds, t]);

  // ── Derived data ──────────────────────────────────────────────────

  // Dynamic filter chips: ALERT_TYPE_LABELS keys filtered to those present in list (finding #2, #20).
  const availableTypes = useMemo(() => {
    const inList = new Set(alerts.map(a => a.alert_type).filter(Boolean));
    const known   = Object.keys(ALERT_TYPE_LABELS).filter(k => inList.has(k));
    const unknown = [...inList].filter(t => !ALERT_TYPE_LABELS[t]);
    return ['all', ...known, ...unknown];
  }, [alerts]);

  // Per-type counts for chip badges.
  const typeCounts = useMemo(() => {
    const map: Record<string, number> = {};
    for (const a of alerts) {
      const t = a.alert_type || 'unknown';
      map[t] = (map[t] ?? 0) + 1;
    }
    return map;
  }, [alerts]);

  // Unique vehicle names for vehicle filter (finding #15).
  const uniqueVehicles = useMemo(
    () => [...new Set(alerts.map(a => a.vehicle_name).filter(Boolean))].sort(),
    [alerts]
  );

  // (Severity summary counts are now derived inside the render block
  //  as `sevCounts` so the filter chips and badges stay in sync.)

  // ── Toggle helpers ────────────────────────────────────────────────
  // useCallback so the AlertCard memo equality check holds across
  // unrelated re-renders (filter changes, history sheet toggles, etc.).

  const toggleExpanded = useCallback((id: number) => {
    setExpandedIds(prev => { const s = new Set(prev); s.has(id) ? s.delete(id) : s.add(id); return s; });
  }, []);
  const toggleAbsolute = useCallback((id: number) => {
    setAbsoluteIds(prev => { const s = new Set(prev); s.has(id) ? s.delete(id) : s.add(id); return s; });
  }, []);

  // ── Loading / error states ────────────────────────────────────────

  if (loading) return <ListRowsSkeleton count={4} />;

  if (error) {
    const ErrIcon =
      error.kind === 'auth'    ? Icon24LockOutline
      : error.kind === 'network' ? Icon24GlobeOutline
      : Icon24WarningTriangleOutline;
    const header =
      error.kind === 'auth'    ? 'Session expired'
      : error.kind === 'server'  ? 'Server error'
      : error.kind === 'network' ? 'No connection'
      : 'Failed to load';
    return (
      <div className="centered">
        <Placeholder
          header={header}
          description={error.message}
          action={error.kind === 'auth' ? null : (
            <button className="retry-btn" onClick={() => { setLoading(true); load().finally(() => setLoading(false)); }}>Retry</button>
          )}
        >
          <ErrIcon width={48} height={48} aria-label={header} style={{ opacity: 0.4 }} />
        </Placeholder>
      </div>
    );
  }

  if (alerts.length === 0) {
    return (
      <>
        <div className="centered">
          <Placeholder header={t('alerts.all_clear_header')} description={t('alerts.all_clear_desc')}>
            <Icon24CheckCircleOutline width={48} height={48} style={{ opacity: 0.4 }} />
          </Placeholder>
          <button className="alerts-history-btn" onClick={openHistory}>
            <Icon24HistoryBackwardOutline width={16} height={16} />
            {t('alerts.view_last_n_days', { n: historyDays })}
          </button>
        </div>
        {historyOpen && renderHistorySheet()}
        <Snackbar open={snackOpen} text={snackText} kind={snackKind} onClose={() => setSnackOpen(false)} />
      </>
    );
  }

  // ── History sheet renderer ────────────────────────────────────────

  function renderHistorySheet() {
    return (
      <BottomSheet
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        title={
          <span className="sheet__title-inner">
            <Icon24HistoryBackwardOutline width={18} height={18} />
            {t('alerts.history_title')}
          </span>
        }
        height="full"
      >
        {/* Day range picker — reuses shared sort-bar__btn (finding #12) */}
        <div className="sort-bar" style={{ marginBottom: 12 }}>
          {[7, 30, 90].map(d => (
            <button
              key={d}
              className={`sort-bar__btn${historyDays === d ? ' sort-bar__btn--active' : ''}`}
              onClick={() => { setHistoryDays(d); fetchHistory(d); }}
            >
              {d}d
            </button>
          ))}
        </div>

        {historyLoading && <ListRowsSkeleton count={6} />}

        {!historyLoading && history && history.length === 0 && (
          <div className="centered" style={{ minHeight: 200 }}>
            <Placeholder
              header={t('alerts.no_history_header')}
              description={t('alerts.no_history_desc', { n: historyDays })}
            >
              <Icon24SearchSlashOutline width={40} height={40} style={{ opacity: 0.35 }} />
            </Placeholder>
          </div>
        )}

        {!historyLoading && history && history.map(h => (
          <div key={h.id} className="hist-row">
            <div className="hist-row__main">
              <div className="hist-row__title">
                {ALERT_TYPE_LABELS[h.alert_type?.toLowerCase()] || h.alert_type} · {h.vehicle_name}
              </div>
              <div className="hist-row__sub">
                <RelativeTime iso={h.created_at} timezone={timezone} />
                {h.status === 'acknowledged' && h.acknowledged_at ? ' · acked' : ''}
              </div>
            </div>
            <span className={`status-badge${h.status === 'acknowledged' ? ' moving' : ''}`}>
              {h.status === 'acknowledged' ? 'acked' : h.status}
            </span>
          </div>
        ))}
      </BottomSheet>
    );
  }

  // ── Main render ───────────────────────────────────────────────────

  const filtered = alerts
    .filter(a => typeFilter === 'all' || a.alert_type === typeFilter)
    .filter(a => vehicleFilter === 'all' || a.vehicle_name === vehicleFilter)
    .filter(a => severityFilter === 'all' || alertSeverity(a) === severityFilter);

  // Pagination over the *filtered* list — server already returns up to
  // 500 active alerts; we slice into 50-row pages so the DOM is light
  // on low-end Telegram clients.
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const visible = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  // Counts per severity for the filter-chip badges (computed off the
  // unfiltered list so each chip shows what it would surface).
  const sevCounts = {
    critical: alerts.filter(a => alertSeverity(a) === 'critical').length,
    warning:  alerts.filter(a => alertSeverity(a) === 'warning').length,
    info:     alerts.filter(a => alertSeverity(a) === 'info').length,
  };

  return (
    <div ref={ptr.ref} style={{ overflowY: 'auto', height: '100%' }}>
      {/* PTR indicator */}
      <div className={`ptr ${ptr.pulling > 0 || ptr.refreshing ? 'ptr--active' : ''}`}>
        {ptr.refreshing ? '↻ Refreshing…' : ptr.pulling > 0 ? '↓ Release to refresh' : ''}
      </div>

      {/* Header: count + bulk-ack + history (finding #7) */}
      <div className="alerts-header">
        <span className="alerts-header__count">
          {/* Header counter logic:
              - filtered.length === alerts.length → just "N pending"
              - any filter active → "M of N" so the user can see how much
                they're hiding (M is post-filter, N is server-side total).
              - server returned fewer than reported (page_size cap) →
                still surface the bigger total. */}
          {filtered.length !== alerts.length || totalCount > alerts.length ? (
            <>Showing <strong>{filtered.length}</strong> of <strong>{Math.max(totalCount, alerts.length)}</strong></>
          ) : (
            <>{alerts.length} pending</>
          )}
        </span>
        <div className="alerts-header__actions">
          {alerts.length > 1 && (
            <button
              className="alerts-header__bulk-ack"
              onClick={() => setConfirmBulkAck(true)}
              disabled={bulkAcking}
            >
              <Icon24ChecksOutline width={15} height={15} />
              {bulkAcking ? '…' : `Ack all (${alerts.length})`}
            </button>
          )}
          <button className="alerts-header__history" onClick={openHistory}>
            <Icon24HistoryBackwardOutline width={15} height={15} />
            History
          </button>
        </div>
      </div>

      {/* Severity filter chips — server-authoritative counts.
          Critical and Warning use icons + colored pills so colorblind
          users can still triage.  Tap a chip to filter the list. */}
      <div className="alerts-filter alerts-filter--severity">
        <button
          className={`sort-bar__btn${severityFilter === 'all' ? ' sort-bar__btn--active' : ''}`}
          onClick={() => setSeverityFilter('all')}
        >
          All <span className="alerts-sev-count">{alerts.length}</span>
        </button>
        {sevCounts.critical > 0 && (
          <button
            className={`sort-bar__btn alerts-filter__btn--critical${
              severityFilter === 'critical' ? ' sort-bar__btn--active' : ''
            }`}
            onClick={() => setSeverityFilter('critical')}
          >
            <Icon24WarningTriangleOutline width={13} height={13} />
            Critical <span className="alerts-sev-count">{sevCounts.critical}</span>
          </button>
        )}
        {sevCounts.warning > 0 && (
          <button
            className={`sort-bar__btn alerts-filter__btn--warning${
              severityFilter === 'warning' ? ' sort-bar__btn--active' : ''
            }`}
            onClick={() => setSeverityFilter('warning')}
          >
            Warning <span className="alerts-sev-count">{sevCounts.warning}</span>
          </button>
        )}
        {sevCounts.info > 0 && (
          <button
            className={`sort-bar__btn alerts-filter__btn--info${
              severityFilter === 'info' ? ' sort-bar__btn--active' : ''
            }`}
            onClick={() => setSeverityFilter('info')}
          >
            Info <span className="alerts-sev-count">{sevCounts.info}</span>
          </button>
        )}
      </div>

      {/* Type filter chips — derived from ALERT_TYPE_LABELS + actual list (findings #2, #11, #16, #20) */}
      <div className="alerts-filter">
        {availableTypes.map(t => (
          <button
            key={t}
            className={`sort-bar__btn${typeFilter === t ? ' sort-bar__btn--active' : ''}`}
            onClick={() => setTypeFilter(t)}
          >
            {t === 'all'
              ? `All (${alerts.length})`
              : `${ALERT_TYPE_LABELS[t] ?? t} (${typeCounts[t] ?? 0})`}
          </button>
        ))}
      </div>

      {/* Vehicle filter — only shown for multi-vehicle fleets (finding #15) */}
      {uniqueVehicles.length > 1 && (
        <div className="alerts-filter alerts-filter--vehicles">
          <button
            className={`sort-bar__btn${vehicleFilter === 'all' ? ' sort-bar__btn--active' : ''}`}
            onClick={() => setVehicleFilter('all')}
          >
            All vehicles
          </button>
          {uniqueVehicles.map(v => (
            <button
              key={v}
              className={`sort-bar__btn${vehicleFilter === v ? ' sort-bar__btn--active' : ''}`}
              onClick={() => setVehicleFilter(v)}
            >
              <Icon24TruckOutline width={13} height={13} />
              {v}
            </button>
          ))}
        </div>
      )}

      {/* Empty filtered state (finding #16) */}
      {visible.length === 0 && (
        <div className="centered" style={{ minHeight: 160 }}>
          <Placeholder
            header={t('alerts.no_matches_header')}
            description={`No ${typeFilter !== 'all' ? (ALERT_TYPE_LABELS[typeFilter] ?? typeFilter) : ''} alerts${vehicleFilter !== 'all' ? ` for ${vehicleFilter}` : ''} pending.`}
          >
            <Icon24SearchSlashOutline width={40} height={40} style={{ opacity: 0.35 }} />
          </Placeholder>
        </div>
      )}

      {/* Alert cards — memoized so expanding one card doesn't re-render
          the rest of the list (matters at 50–100 pending alerts). */}
      {visible.map(alert => (
        <AlertCard
          key={alert.id}
          alert={alert}
          isExpanded={expandedIds.has(alert.id)}
          showAbsolute={absoluteIds.has(alert.id)}
          isAcking={acking.has(alert.id)}
          isMuting={muting.has(alert.id)}
          isMuted={mutedIds.has(alert.id)}
          timezone={timezone}
          onToggleAbsolute={toggleAbsolute}
          onToggleExpanded={toggleExpanded}
          onAcknowledge={acknowledge}
          onMute={muteAlert}
        />
      ))}

      {/* Pager — Prev / "Page N of M" / Next.  Only shown when the
          (post-filter) list spans more than one page. */}
      {totalPages > 1 && (
        <div className="alerts-pager">
          <button
            className="alerts-pager__btn"
            onClick={() => setPage(p => Math.max(1, p - 1))}
            disabled={safePage <= 1}
            aria-label="Previous page"
          >
            <Icon24ChevronLeftOutline width={16} height={16} />
            Prev
          </button>
          <span className="alerts-pager__label">
            Page <strong>{safePage}</strong> of <strong>{totalPages}</strong>
          </span>
          <button
            className="alerts-pager__btn"
            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
            disabled={safePage >= totalPages}
            aria-label="Next page"
          >
            Next
            <Icon24ChevronRightOutline width={16} height={16} />
          </button>
        </div>
      )}

      {historyOpen && renderHistorySheet()}

      {/* Ack-all confirmation — destructive, no per-row undo so we ask. */}
      <BottomSheet
        open={confirmBulkAck}
        onClose={() => setConfirmBulkAck(false)}
        title="Acknowledge all alerts?"
      >
        <p style={{ color: 'var(--tgui--hint_color)', fontSize: 14, marginTop: 0 }}>
          {`This will mark ${alerts.length === 1 ? 'this' : 'all'} ${pluralize(alerts.length, 'pending alert')} as acknowledged. You can review them in History.`}
        </p>
        <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
          <button
            className="profile-secondary-btn"
            style={{ flex: 1 }}
            onClick={() => setConfirmBulkAck(false)}
          >
            Cancel
          </button>
          <button
            className="profile-save-btn"
            style={{ flex: 1 }}
            disabled={bulkAcking}
            onClick={() => { setConfirmBulkAck(false); bulkAck(); }}
          >
            {bulkAcking ? '…' : 'Acknowledge'}
          </button>
        </div>
      </BottomSheet>

      <Snackbar open={snackOpen} text={snackText} kind={snackKind} onClose={() => setSnackOpen(false)} />
    </div>
  );
}
