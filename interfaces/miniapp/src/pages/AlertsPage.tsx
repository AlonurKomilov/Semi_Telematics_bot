/**
 * AlertsPage — pending alerts list with optimistic ack + history view.
 *
 * Layout: 2-row alert cards (title + vehicle name + when, message),
 * full-width Done button with icon, swipe-friendly tap targets.
 * Empty state offers a quick jump to the last-N-days history.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
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
} from '@vkontakte/icons';
import { Placeholder } from '@telegram-apps/telegram-ui';
import { apiFetch, apiJSON } from '../api/client';
import type { Alert } from '../types';
import { Snackbar } from '../components/Snackbar';
import { BottomSheet } from '../components/BottomSheet';
import { RelativeTime } from '../components/RelativeTime';
import { ListRowsSkeleton } from '../components/Skeleton';
import { haptics } from '../hooks/useTelegram';
import { usePullToRefresh } from '../hooks/usePullToRefresh';

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
  const t = a.alert_type?.toLowerCase() ?? '';
  if (t === 'crash' || t === 'rollover') return 'critical';
  if (t === 'fuel' || t === 'idle' || t === 'maintenance') return 'info';
  return 'warning';
}

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

export function AlertsPage({ active, onCountChange, refreshKey, timezone }: Props) {
  const [alerts, setAlerts]           = useState<Alert[]>([]);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState(false);
  const [acking, setAcking]           = useState<Set<number>>(new Set());
  const [bulkAcking, setBulkAcking]   = useState(false);
  const [snackOpen, setSnackOpen]     = useState(false);
  const [snackText, setSnackText]     = useState('');
  const [snackKind, setSnackKind]     = useState<'success' | 'error' | 'info'>('info');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory]         = useState<AlertHistoryItem[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyDays, setHistoryDays] = useState(7);
  const [typeFilter, setTypeFilter]   = useState<string>('all');
  const [vehicleFilter, setVehicleFilter] = useState<string>('all');
  // Per-card expanded message & absolute timestamp toggles.
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [absoluteIds, setAbsoluteIds] = useState<Set<number>>(new Set());

  function toast(text: string, kind: 'success' | 'error' | 'info' = 'info') {
    setSnackText(text); setSnackKind(kind); setSnackOpen(true);
  }

  const load = useCallback(async () => {
    setError(false);
    try {
      const data = await apiJSON<{ alerts: Alert[] }>('/api/alerts/pending');
      // Sort newest-first (finding #18 — explicit ordering).
      const arr = (data.alerts ?? []).sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
      setAlerts(arr);
      onCountChange?.(arr.length);
    } catch (e) {
      console.error('Failed to load alerts:', e);
      setError(true);
    }
  }, [onCountChange]);

  useEffect(() => {
    setLoading(true);
    load().finally(() => setLoading(false));
  }, [load]);

  useEffect(() => { if (active) load(); }, [active, load]);

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
      toast(`Acknowledged all ${ids.length} alerts`, 'success');
    } catch (e) {
      console.error('Bulk ack failed:', e);
      setAlerts(snapshot);
      onCountChange?.(snapshot.length);
      haptics.error();
      toast('Failed to acknowledge — try again', 'error');
    } finally {
      setBulkAcking(false);
    }
  }

  async function acknowledge(id: number) {
    setAcking(prev => new Set(prev).add(id));
    const snapshot = alerts.find(a => a.id === id);
    setAlerts(prev => {
      const next = prev.filter(a => a.id !== id);
      onCountChange?.(next.length);
      return next;
    });
    try {
      await apiFetch(`/api/alerts/${id}/acknowledge`, { method: 'POST' });
      haptics.success();
      toast('Alert acknowledged', 'success');
    } catch (e) {
      console.error('Failed to acknowledge alert:', e);
      if (snapshot) {
        setAlerts(prev => {
          const next = [snapshot, ...prev.filter(a => a.id !== id)];
          onCountChange?.(next.length);
          return next;
        });
      }
      haptics.error();
      toast('Failed to acknowledge — try again', 'error');
    } finally {
      setAcking(prev => { const next = new Set(prev); next.delete(id); return next; });
    }
  }

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

  // Severity summary counts (finding #8).
  const criticalCount = alerts.filter(a => alertSeverity(a) === 'critical').length;
  const warningCount  = alerts.filter(a => alertSeverity(a) === 'warning').length;

  // ── Toggle helpers ────────────────────────────────────────────────

  function toggleExpanded(id: number) {
    setExpandedIds(prev => { const s = new Set(prev); s.has(id) ? s.delete(id) : s.add(id); return s; });
  }
  function toggleAbsolute(id: number) {
    setAbsoluteIds(prev => { const s = new Set(prev); s.has(id) ? s.delete(id) : s.add(id); return s; });
  }

  // ── Loading / error states ────────────────────────────────────────

  if (loading) return <ListRowsSkeleton count={4} />;

  if (error) {
    return (
      <div className="centered">
        <Placeholder
          header="Failed to Load"
          description="Could not fetch your alerts. Check your connection and retry."
          action={<button className="retry-btn" onClick={() => { setLoading(true); load().finally(() => setLoading(false)); }}>Retry</button>}
        >
          <Icon24WarningTriangleOutline width={48} height={48} style={{ opacity: 0.4 }} />
        </Placeholder>
      </div>
    );
  }

  if (alerts.length === 0) {
    return (
      <>
        <div className="centered">
          <Placeholder header="All Clear" description="No pending alerts — you're all set.">
            <Icon24CheckCircleOutline width={48} height={48} style={{ opacity: 0.4 }} />
          </Placeholder>
          <button className="alerts-history-btn" onClick={openHistory}>
            <Icon24HistoryBackwardOutline width={16} height={16} />
            View last {historyDays} days
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
            Alert History
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
              header="No history"
              description={`Nothing in the last ${historyDays} days.`}
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

  const visible = alerts
    .filter(a => typeFilter === 'all' || a.alert_type === typeFilter)
    .filter(a => vehicleFilter === 'all' || a.vehicle_name === vehicleFilter);

  return (
    <div ref={ptr.ref} style={{ overflowY: 'auto', height: '100%' }}>
      {/* PTR indicator */}
      <div className={`ptr ${ptr.pulling > 0 || ptr.refreshing ? 'ptr--active' : ''}`}>
        {ptr.refreshing ? '↻ Refreshing…' : ptr.pulling > 0 ? '↓ Release to refresh' : ''}
      </div>

      {/* Header: count + bulk-ack + history (finding #7) */}
      <div className="alerts-header">
        <span className="alerts-header__count">{alerts.length} pending</span>
        <div className="alerts-header__actions">
          {alerts.length > 1 && (
            <button className="alerts-header__bulk-ack" onClick={bulkAck} disabled={bulkAcking}>
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

      {/* Severity summary row (finding #8) */}
      {(criticalCount > 0 || warningCount > 0) && (
        <div className="alerts-severity-row">
          {criticalCount > 0 && (
            <span className="alerts-severity-pill alerts-severity-pill--critical">
              <Icon24WarningTriangleOutline width={13} height={13} />
              {criticalCount} critical
            </span>
          )}
          {warningCount > 0 && (
            <span className="alerts-severity-pill alerts-severity-pill--warning">
              {warningCount} warning
            </span>
          )}
        </div>
      )}

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
            header="No matches"
            description={`No ${typeFilter !== 'all' ? (ALERT_TYPE_LABELS[typeFilter] ?? typeFilter) : ''} alerts${vehicleFilter !== 'all' ? ` for ${vehicleFilter}` : ''} pending.`}
          >
            <Icon24SearchSlashOutline width={40} height={40} style={{ opacity: 0.35 }} />
          </Placeholder>
        </div>
      )}

      {/* Alert cards */}
      {visible.map(alert => {
        const sev = alertSeverity(alert);
        const isExpanded = expandedIds.has(alert.id);
        const showAbsolute = absoluteIds.has(alert.id);
        const hasLongMsg = (alert.message?.length ?? 0) > 80;

        return (
          <div
            key={alert.id}
            className={`alert-card${sev === 'critical' ? ' alert-card--critical' : ''}`}
          >
            <div className={`alert-card__strip alert-card__strip--${sev}`} />

            {/* VK icon instead of emoji (findings #1, #2) */}
            <div className={`alert-card__icon-wrap alert-card__icon-wrap--${sev}`} aria-hidden>
              <AlertIcon alert={alert} />
            </div>

            <div className="alert-card__main">
              <div className="alert-card__row1">
                {/* Title only — vehicle name below (finding #4) */}
                <span className="alert-card__title">{alertTitle(alert)}</span>
                {/* Tap timestamp to toggle relative ↔ absolute (finding #19) */}
                <span
                  className="alert-card__when"
                  onClick={() => toggleAbsolute(alert.id)}
                  title="Tap for exact time"
                >
                  {showAbsolute
                    ? formatAbsolute(alert.created_at)
                    : <RelativeTime iso={alert.created_at} timezone={timezone} />}
                </span>
              </div>

              {/* Vehicle name as separate element (finding #4) */}
              {alert.vehicle_name && (
                <div className="alert-card__vehicle">
                  <Icon24TruckOutline width={11} height={11} />
                  {alert.vehicle_name}
                </div>
              )}

              {/* Expandable message — tap to reveal full text (finding #17) */}
              {alert.message && (
                <div
                  className={`alert-card__msg${isExpanded ? ' alert-card__msg--expanded' : ''}`}
                  onClick={hasLongMsg ? () => toggleExpanded(alert.id) : undefined}
                  style={hasLongMsg ? { cursor: 'pointer' } : undefined}
                >
                  {alert.message}
                </div>
              )}
            </div>

            {/* Done button with icon (finding #3) */}
            <button
              className="alert-card__ack"
              disabled={acking.has(alert.id)}
              onClick={() => acknowledge(alert.id)}
              aria-label={`Acknowledge ${alertTitle(alert)} on ${alert.vehicle_name}`}
            >
              {acking.has(alert.id)
                ? '…'
                : <><Icon24DoneOutline width={16} height={16} />Done</>}
            </button>
          </div>
        );
      })}

      {historyOpen && renderHistorySheet()}
      <Snackbar open={snackOpen} text={snackText} kind={snackKind} onClose={() => setSnackOpen(false)} />
    </div>
  );
}
