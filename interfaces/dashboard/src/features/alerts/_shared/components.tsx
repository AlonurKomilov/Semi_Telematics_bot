/**
 * Shared presentational components for Alerts sections.
 *
 * Pure presentational — no state, no hooks.  Sections like the
 * future SafetySummaryStrip and VehicleHealthSummary reuse TypeBadge
 * + SeverityDot to keep the visual language consistent across the
 * feature.
 */
import { CheckCircle2 } from 'lucide-react';
import { statusTone, toneText } from '../../../lib/status';
import { formatDate } from '../../../utils/datetime';
import type { Alert } from '../../../types';
import { Tip } from '../../../components/tooltip';

// Raw alert_type → the SAME words the Type filter chips show, so a row and
// the control that filters it name the thing identically.  Multi-word keys
// need the map (CSS `capitalize` would render "Doc_expiry").
const TYPE_TEXT: Record<string, string> = {
  fault: 'Fault', faults: 'Fault',
  health: 'Health',
  fuel: 'Fuel',
  events: 'Events', event: 'Events', safety_events: 'Events',
  parking: 'Parking',
  camera: 'Camera',
  geofence: 'Geofence',
  maintenance: 'Maintenance',
  documents: 'Documents', doc_expiry: 'Documents',
  scorecard: 'Scorecard',
  system: 'System', samsara_sync: 'Sync', reescalate: 'Re-escalation',
};

function typeText(type: string): string {
  return TYPE_TEXT[type]
    ?? type.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/** What KIND of alert this is — identity, not severity.
 *
 *  Neutral on purpose.  The row already carries one colour channel (the
 *  severity dot + word); tinting the type as well made the two compete for
 *  the same glance, and the five hues it used were raw Tailwind palette
 *  values, which the design system forbids (there is no categorical colour
 *  token here — the theme is monochrome with semantic accents).
 *
 *  Shape is the grammar: a borderless PILL means "this is a fact about the
 *  row"; the bordered rounded-rect chips in the filter bar mean "click me
 *  to filter". Same words, different shapes, no ambiguity. */
export function TypeBadge({ type }: { type: string }) {
  return (
    <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-muted text-muted-foreground">
      {typeText(type)}
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
  // Severity → tone via the shared status map (critical→danger,
  // warning→warn, info→info); one tone drives both the dot fill and
  // the label text so they can't drift apart.
  const tone = statusTone(sev);
  const label = { critical: 'Critical', warning: 'Warning', info: 'Info' }[sev];
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${toneText(tone)}`}>
      <span className={`w-2 h-2 rounded-full bg-${tone}`} aria-hidden />
      {label}
    </span>
  );
}

/**
 * Resolution marker for a non-active alert.  A human ack carries a
 * name (acknowledged_by > 0, resolved server-side to a display name);
 * a self-cleared alert has no actor and reads "Auto-resolved".  Active
 * alerts render nothing here.
 */
export function AckMarker({ alert, tz }: { alert: Alert; tz?: string }) {
  if ((alert.status ?? 'active') === 'active') return null;
  const human = (alert.acknowledged_by ?? 0) > 0;
  const when = alert.acknowledged_at
    ? formatDate(alert.acknowledged_at, { timeZone: tz })
    : '';
  if (human) {
    const who = alert.acknowledged_by_name || 'user';
    return (
      <Tip label={when ? `Acknowledged ${when}` : ''}>
        <span className="inline-flex items-center gap-1 text-xs font-medium text-ok">
          <CheckCircle2 size={14} aria-hidden />
          Acknowledged by {who}
        </span>
      </Tip>
    );
  }
  return (
    <Tip label={when ? `Auto-resolved ${when}` : ''}>
      <span className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground">
        <CheckCircle2 size={14} aria-hidden />
        Auto-resolved
      </span>
    </Tip>
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
