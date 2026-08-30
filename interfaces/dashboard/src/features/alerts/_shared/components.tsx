/**
 * Shared presentational components for Alerts sections.
 *
 * Pure presentational — no state, no hooks.  Sections like the
 * future SafetySummaryStrip and VehicleHealthSummary reuse TypeBadge
 * + SeverityDot to keep the visual language consistent across the
 * feature.
 */
import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, Eye } from 'lucide-react';
import { Avatar, AvatarFallback } from '../../../components/ui/avatar';
import { statusTone, toneText } from '../../../lib/status';
import { formatDate } from '../../../utils/datetime';
import type { Alert } from '../../../types';
import { Tip } from '../../../components/tooltip';
import { observeSeen, wasSeenThisSession } from './seenReporter';

// Raw alert_type → the row's noun when no per-row kind is stored.  The
// Feature column carries the family, so the type label names the THING
// ("Low Fuel", not a "Fuel" echo of a Vehicles feature).  The Type
// filter options derive their labels from this map, so chip and badge
// always read the same word (owner decision 2026-07-27).
const TYPE_TEXT: Record<string, string> = {
  fault: 'Engine Fault', faults: 'Engine Fault',
  health: 'Engine Health',
  fuel: 'Low Fuel',
  events: 'Events', event: 'Events', safety_events: 'Events',
  parking: 'Parking',
  camera: 'Camera Issue',
  geofence: 'Geofence',
  maintenance: 'Maintenance',
  documents: 'Documents', doc_expiry: 'Documents',
  scorecard: 'Scorecard',
  system: 'System', samsara_sync: 'Sync', reescalate: 'Re-escalation',
};

// Per-row kind → display noun.  Kinds arrive from the server only when
// the stored row provably encodes one (Alert.kind): safety-event
// sub-kinds (mirrors SUBTYPE_LABELS in the routing UI — guarded by
// familyText.test.ts) and the parking classifier's location class
// ('unknown' renders as Unverified, matching the alert message).
const KIND_TEXT: Record<string, string> = {
  crash: 'Crash', braking: 'Harsh Braking', acceleration: 'Harsh Acceleration',
  harshTurn: 'Harsh Turn', rollingStop: 'Rolling Stop',
  followingDistance: 'Following Distance', laneDeparture: 'Lane Departure',
  unsafe: 'Unsafe Parking', unknown: 'Unverified Parking',
};

// Alert type → owning FEATURE, named as the feature catalog names it
// (featureCatalog.ts ids → their English nav labels; familyText.test.ts
// guards the mirror).  Answers "which feature does this alert belong
// to?": Fuel is a child of Vehicles, Events of Safety Events.  NEVER a
// persona word — "Safety" is a role here, not a feature (PERSONA.md).
// English product vocabulary, same rule as TYPE_TEXT.
const TYPE_FAMILY: Record<string, string> = {
  fault: 'Vehicles', faults: 'Vehicles', health: 'Vehicles', fuel: 'Vehicles',
  events: 'Safety Events', event: 'Safety Events', safety_events: 'Safety Events',
  camera: 'Cameras',
  parking: 'Parking',
  geofence: 'Geofences',
  scorecard: 'Scorecards',
  maintenance: 'Maintenance',
  documents: 'Drivers', doc_expiry: 'Drivers',
  // Owner/admin housekeeping (sync failures, re-escalations) — not a
  // catalog feature; the pipeline homes these on the system route.
  system: 'System', samsara_sync: 'System', reescalate: 'System',
};

export function familyText(type: string): string {
  return TYPE_FAMILY[type] ?? 'Other';
}

// The alert_type values rows can actually carry in alert_history — the
// vocabulary the server filter, the persona defaults, and the Type
// options must all stay inside.  (fault is stored SINGULAR.)
export const STORED_ALERT_TYPES = [
  'fault', 'health', 'fuel', 'events', 'camera', 'parking',
  'geofence', 'maintenance', 'documents', 'scorecard',
] as const;

export function kindText(kind: string): string {
  return KIND_TEXT[kind] ?? '';
}

export function typeText(type: string): string {
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
export function TypeBadge({ type, kind }: { type: string; kind?: string }) {
  return (
    <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-muted text-muted-foreground">
      {(kind && KIND_TEXT[kind]) || typeText(type)}
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

function initialsOf(name: string): string {
  return name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();
}

/**
 * The one Status cell an ACTIVE row gets: "New" until somebody's screen
 * has actually shown it, then "👁 AK, JD" — the people who saw it, in
 * the order they looked (owner decision 2026-08-30: one shared column
 * for the whole account, no per-viewer tabs; your own absence from the
 * list IS your "new to me").
 *
 * The cell observes ITSELF for visibility, which is what makes "Seen"
 * honest: reported only after this row was ≥50% on screen for ≥1s,
 * batched by the shared reporter — never "page loaded, mark a hundred
 * seen".  Nothing here touches acknowledgment: eyes are exposure,
 * acknowledge is responsibility, and escalation keys on the latter
 * alone.
 */
function SeenMarker({ alert }: { alert: Alert }) {
  // Alert.id is string|number across the two feeds; the ledger keys on
  // the numeric history id.
  const aid = Number(alert.id);
  const ref = useRef<HTMLSpanElement | null>(null);
  const [mineNow, setMineNow] = useState(() => wasSeenThisSession(aid));
  useEffect(() => {
    if (!ref.current || !Number.isFinite(aid)) return;
    return observeSeen(ref.current, aid, () => setMineNow(true));
  }, [aid]);

  const viewers = alert.seen_by ?? [];
  const mine = mineNow || !!alert.seen_by_me;
  const names = viewers.map((v) => v.name).filter(Boolean);
  // A view this session that the server response predates still shows —
  // otherwise the row you are looking at claims nobody has looked at it.
  if (mine && !alert.seen_by_me) names.push('You');

  if (names.length === 0 && !mine) {
    return <span ref={ref} className="text-xs font-medium text-foreground">New</span>;
  }
  const shown = names.slice(0, 3).map(initialsOf).join(', ');
  const extra = names.length - 3;
  return (
    <Tip label={`Seen by ${names.join(', ')}`}>
      <span ref={ref}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground">
        <Eye className="size-3.5" aria-hidden />
        {shown}{extra > 0 && ` +${extra}`}
      </span>
    </Tip>
  );
}

/**
 * The Status cell — the row's whole lifecycle in one column, in order:
 * New → 👁 seen-by → Acknowledged by a person / Auto-resolved.  A human
 * ack carries a name (acknowledged_by > 0, resolved server-side) and
 * renders as an initials AVATAR + name — attribution at a glance, and
 * the human/machine contrast does the work: people get a face, a
 * self-cleared alert stays muted "Auto-resolved" text (owner decision
 * 2026-07-27).  The ack time lives in the tooltip.
 */
export function AckMarker({ alert, tz }: { alert: Alert; tz?: string }) {
  if ((alert.status ?? 'active') === 'active') {
    return <SeenMarker alert={alert} />;
  }
  const human = (alert.acknowledged_by ?? 0) > 0;
  const when = alert.acknowledged_at
    ? formatDate(alert.acknowledged_at, { timeZone: tz })
    : '';
  if (human) {
    const who = alert.acknowledged_by_name || 'user';
    // Same initials recipe as the shell's AvatarMenu, so the same
    // person renders the same chip everywhere.
    const initials = who
      .split(' ')
      .map((w) => w[0])
      .join('')
      .slice(0, 2)
      .toUpperCase();
    return (
      <Tip label={when ? `Acknowledged ${when}` : ''}>
        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-foreground">
          <Avatar size="sm" className="shrink-0">
            <AvatarFallback className="bg-ok/20 text-ok text-2xs font-semibold">
              {initials}
            </AvatarFallback>
          </Avatar>
          {who}
        </span>
      </Tip>
    );
  }
  return (
    <Tip label={when ? `Auto-resolved ${when}` : ''}>
      <span className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground">
        <CheckCircle2 className="size-3.5" aria-hidden />
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
