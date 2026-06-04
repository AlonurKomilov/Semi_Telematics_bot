/**
 * Shared presentational components for Alerts sections.
 *
 * Pure presentational — no state, no hooks.  Sections like the
 * future SafetySummaryStrip and VehicleHealthSummary reuse TypeBadge
 * + SeverityDot to keep the visual language consistent across the
 * feature.
 */
import { CheckCircle2 } from 'lucide-react';
import type { Alert } from '../../../types';

export function TypeBadge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    fault: 'bg-orange-500/15 text-orange-700 dark:text-orange-400',
    health: 'bg-red-500/15 text-red-700 dark:text-red-400',
    fuel: 'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400',
    events: 'bg-purple-500/15 text-purple-700 dark:text-purple-400',
    parking: 'bg-cyan-500/15 text-cyan-700 dark:text-cyan-400',
  };
  const cls = colors[type] || 'bg-gray-500/20 text-muted-foreground';
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>
      {type}
    </span>
  );
}

/**
 * Severity dot + label.  Reads ``alert_history.severity``
 * (server-authoritative) so it always matches the bot's per-type
 * formatter.  Falls back to 'warning' for legacy rows that haven't
 * been migrated yet.
 */
export function SeverityDot({ severity }: { severity?: string }) {
  const sev = severity === 'critical' || severity === 'info' ? severity : 'warning';
  const cfg: Record<string, { dot: string; text: string; label: string }> = {
    critical: { dot: 'bg-red-500', text: 'text-red-600 dark:text-red-400', label: 'Critical' },
    warning: { dot: 'bg-orange-500', text: 'text-orange-600 dark:text-orange-400', label: 'Warning' },
    info: { dot: 'bg-blue-500', text: 'text-blue-600 dark:text-blue-400', label: 'Info' },
  };
  const c = cfg[sev];
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${c.text}`}>
      <span className={`w-2 h-2 rounded-full ${c.dot}`} aria-hidden />
      {c.label}
    </span>
  );
}

/**
 * Resolution marker for a non-active alert.  A human ack carries a
 * name (acknowledged_by > 0, resolved server-side to a display name);
 * a self-cleared alert has no actor and reads "Auto-resolved".  Active
 * alerts render nothing here.
 */
export function AckMarker({ alert }: { alert: Alert }) {
  if ((alert.status ?? 'active') === 'active') return null;
  const human = (alert.acknowledged_by ?? 0) > 0;
  const when = alert.acknowledged_at
    ? new Date(alert.acknowledged_at).toLocaleString()
    : '';
  if (human) {
    const who = alert.acknowledged_by_name || 'user';
    return (
      <span
        className="inline-flex items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400"
        title={when ? `Acknowledged ${when}` : undefined}
      >
        <CheckCircle2 size={13} aria-hidden />
        Acknowledged by {who}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground"
      title={when ? `Auto-resolved ${when}` : undefined}
    >
      <CheckCircle2 size={13} aria-hidden />
      Auto-resolved
    </span>
  );
}

export function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

/**
 * Only un-acknowledged (status='active') alerts can be acknowledged
 * — re-acking a cleared alert is a no-op.  Selection + the bulk-ack
 * count are scoped to ackable rows so "Acknowledge (N)" always means
 * "N alerts that still need it", even in the All / Acknowledged
 * views.
 */
export function isAckable(a: Alert): boolean {
  return (a.status ?? 'active') === 'active';
}
