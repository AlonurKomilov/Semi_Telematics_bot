/**
 * Parking status badges — shared by the grid columns and the detail view.
 *
 * Extracted from Parking.tsx unchanged.  They live here rather than beside
 * the columns because the detail panel renders the same badges, and a
 * panel importing from a file called "columns" would misdescribe what it
 * depends on.
 */
import { toneClasses, statusClasses, type Tone } from '../../lib/status';
import { Badge } from '@/components/ui/badge';

// Parking classification → tone: safe/geofence read as good (ok),
// unsafe is the danger signal, unknown carries no signal (neutral).
export const CLASS_TONE: Record<string, Tone> = {
  safe:     'ok',
  geofence: 'ok',
  unsafe:   'danger',
  unknown:  'neutral',
};

// Two badges from two unrelated dimensions sit side by side: WHERE it
// parked (location_class) and HOW BAD that is (alert_level).  Rendered as
// bare pills they read as one phrase — "UNKNOWN CRITICAL" looks like a
// single self-contradicting status, so each gets its dimension named
// before the value.
//
// A label plus whatever badge already renders that value — NOT a second
// implementation of the tone mapping.  Wrapping the existing badges keeps
// one source of truth for colour (including breakdown's categorical
// purple) and cannot drift from them.
export function DimBadge({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs">
      <span className="text-muted-foreground">{label}</span>
      {children}
    </span>
  );
}

export function ConfidenceBadge({ value }: { value: string }) {
  const v = value.toLowerCase();
  const tone: Tone = v.startsWith('high') ? 'ok'
    : v.startsWith('med') ? 'warn'
    : v.startsWith('low') ? 'danger'
    : 'neutral';
  return <Badge tone={tone} className="uppercase">{value}</Badge>;
}

/** Active vs Resolved — the column that replaced the Active/History tabs.
 *
 *  Deliberately NOT a tone badge: "resolved" is not success and "active"
 *  is not danger — severity is already carried by AlertBadge next door,
 *  and colouring status too would give one row two competing alarm
 *  signals.  Active reads as the live state (foreground), resolved as
 *  settled (muted). */
export function StatusBadge({ resolved }: { resolved: unknown }) {
  return resolved
    ? <span className="px-2 py-0.5 rounded-md text-xs font-medium bg-muted text-muted-foreground">Resolved</span>
    : <span className="px-2 py-0.5 rounded-md text-xs font-medium bg-foreground/10 text-foreground">Active</span>;
}

export function ClassBadge({ cls }: { cls: string }) {
  const c = toneClasses(CLASS_TONE[cls] ?? 'neutral');
  return <span className={`px-2 py-0.5 rounded-md text-xs font-medium uppercase ${c}`}>{cls}</span>;
}

// Alert level → tone, resolved by the shared status map (lib/status.ts)
// rather than a local ternary, so parking's "critical" cannot drift from
// every other feature's.
//
// ``breakdown`` used to carry a bespoke raw-Tailwind purple here to mark
// it as a different KIND of event rather than a severity step.  The kind
// is what the LABEL says — "BREAKDOWN" vs "CRITICAL", both already
// rendered — while colour stays a pure severity channel app-wide; a hue
// that appears in exactly one badge teaches the operator nothing they
// can reuse.  It now maps to danger in the status map, where the reason
// is recorded next to every other status.
export function AlertBadge({ level }: { level: string }) {
  return (
    <span className={`px-2 py-0.5 rounded-md text-xs font-medium uppercase ${statusClasses(level)}`}>
      {level}
    </span>
  );
}
