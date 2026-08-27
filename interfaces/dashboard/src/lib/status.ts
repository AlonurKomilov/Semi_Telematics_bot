/**
 * Status colour — the single source of truth.
 * ============================================
 *
 * Every "what does this state mean" colour in the dashboard funnels
 * through here.  Two layers:
 *
 *   1.  `Tone` — the four semantic meanings plus a neutral.  These map
 *       onto the `--ok` / `--warn` / `--danger` / `--info` CSS tokens
 *       (defined in index.css, registered in tailwind.config.js), which
 *       flip light↔dark automatically.  `neutral` reuses the existing
 *       muted/border surface tokens — there is no separate "grey" hue.
 *
 *   2.  `toneClasses(tone)` — the canonical "soft pill" recipe:
 *           bg-<hue>-bg (15% fill) + text-<hue> (solid) + border-<hue>-bd (30%)
 *
 *       THE BORDER WIDTH IS PART OF THE RECIPE — it did not used to be.
 *       `border-<hue>-bd` sets border-COLOUR only, and Tailwind's
 *       preflight zeroes border-width on every element, so for a long
 *       time this helper painted a 30% colour onto a 0px edge: at 139
 *       of 213 call-sites the documented border simply did not exist.
 *       It went unnoticed for the obvious reason — a missing hairline
 *       reads as a deliberate flat chip, not as a bug.  The width now
 *       ships with the recipe so `toneClasses()` delivers what
 *       design.md §3 promises.
 *
 *       The opt-OUT exists for one geometric reason: the pill is
 *       border-box, so a hairline steals 2px of content height.  An
 *       element with a FIXED height whose line-height fills it (the
 *       board's `h-6 leading-6` chips) overflows its own box once a
 *       border appears.  Those pass `{ border: false }` — a deliberate,
 *       greppable exception, not an accident:
 *
 *           toneClasses('warn')                   → fill + text + 1px edge
 *           toneClasses('warn', { border: false }) → fill + text, no edge
 *
 *       This is the treatment that was hand-written ~900 times across
 *       the app as `bg-green-500/15 text-green-700 dark:text-green-400
 *       border-green-500/30`.  Now it lives once.  NB: the alpha is
 *       pre-baked into the `-bg`/`-bd` tokens (color-mix, in index.css)
 *       so the recipe is one short class set.  `bg-ok/15` also works now
 *       (the `/<alpha>` modifier is enabled on every token by
 *       tokenColor() in tailwind.config.js) — we just prefer the baked
 *       tokens here so the soft-pill is byte-identical everywhere.
 *
 * Why a helper and not just classes: most call-sites start from a
 * *domain* string ("overdue", "in_progress", "moving") rather than a
 * tone.  `statusTone()` owns that mapping so a status's colour can't
 * drift between the table, the badge, and the chart that all render it.
 *
 * Adding a new status?  Map it to an existing Tone here — do NOT invent
 * a new colour at the call-site.  If a genuinely new meaning is needed,
 * add a token in index.css first, then a Tone here.
 */
import {
  AlertTriangle, CheckCircle2, Info, XCircle, type LucideIcon,
} from 'lucide-react';


export type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral';

const TONE_CLASSES: Record<Tone, string> = {
  ok:      'bg-ok-bg text-ok border-ok-bd',
  warn:    'bg-warn-bg text-warn border-warn-bd',
  danger:  'bg-danger-bg text-danger border-danger-bd',
  info:    'bg-info-bg text-info border-info-bd',
  neutral: 'bg-muted text-muted-foreground border-border',
};

/**
 * Soft-pill class recipe for a tone: bg /15 fill + solid text + the /30
 * border, width included.  Pass `{ border: false }` only where a fixed
 * height leaves no room for the hairline — see the header note.
 */
export function toneClasses(tone: Tone, opts?: { border?: boolean }): string {
  return opts?.border === false ? TONE_CLASSES[tone] : `${TONE_CLASSES[tone]} border`;
}

/** Just the solid foreground colour class — for icons, dots, chart bars. */
export function toneText(tone: Tone): string {
  return tone === 'neutral' ? 'text-muted-foreground' : `text-${tone}`;
}

/**
 * The ICON that means a tone.  Lives here, beside the colours, because
 * a tone is a meaning and its icon is half of how that meaning reads —
 * this map was previously a private const inside AppBanner, so a
 * warning in a toast and a warning pinned to a card could drift apart
 * with nobody noticing.  Both lanes import it now, which makes them
 * consistent by construction rather than by discipline.
 */
export const TONE_ICON: Record<Tone, LucideIcon> = {
  ok:      CheckCircle2,
  warn:    AlertTriangle,
  danger:  XCircle,
  info:    Info,
  neutral: Info,
};

/** The icon component for a tone — `toneText`'s counterpart. */
export function toneIcon(tone: Tone): LucideIcon {
  return TONE_ICON[tone];
}

/**
 * Domain status string → Tone.  Case-insensitive.  Covers the vehicle,
 * maintenance, priority, and generic lifecycle vocabularies that were
 * previously each given their own colour map.  Unknown values fall back
 * to `neutral` so a typo reads as "no signal", never as a wrong signal.
 */
const STATUS_TONE: Record<string, Tone> = {
  // Vehicle telematics
  moving: 'ok', active: 'ok', on: 'ok', online: 'ok',
  idle: 'warn',
  stopped: 'danger', inactive: 'danger', offline: 'danger',
  off: 'neutral', unknown: 'neutral',
  // Not the same claim as 'unknown': we KNOW what happened —
  // the device sent nothing.  Stating the fact beats confessing
  // confusion, and it matches the words the rest of the page
  // already uses for it.
  'no data': 'neutral',
  // Registry vehicle with no telematics match (trailer / not-yet-
  // equipped truck) — neutral, not red: it's a deliberate state, not
  // a fault.
  no_telemetry: 'neutral',
  // A truck that LEFT the fleet.  Neutral for the same reason as
  // no_telemetry: it is a decision someone made, not a failure.  The
  // sibling `inactive` is danger, which is right for a thing that
  // stopped working and wrong for a thing that was retired on purpose.
  archived: 'neutral',
  // Task / work-order lifecycle.  Urgency progression reads as
  // info → warn → danger so an operator can tell the severity at a
  // glance: pending=info (scheduled, no action needed), due_soon=warn
  // (act this week), overdue=danger (act now).  Pending used to be
  // warn which collapsed it visually with due_soon — confusing on a
  // long list.
  pending: 'info', in_progress: 'info', scheduled: 'info',
  due_soon: 'warn',
  overdue: 'danger', failed: 'danger', error: 'danger',
  completed: 'ok', done: 'ok', resolved: 'ok', paid: 'ok',
  cancelled: 'neutral', canceled: 'neutral', draft: 'neutral',
  // Load lifecycle (the Loads feature): upcoming/dispatched are planned
  // states (info), in_transit is actively earning (ok), delivered lands
  // as done (ok), unpaid flags follow-up (warn).
  upcoming: 'info', dispatched: 'info', in_transit: 'ok',
  delivered: 'ok', unpaid: 'warn',
  // Onboard inventory (Vehicle > Inventory card) — installed is healthy,
  // needs_check / in_repair await action, damaged / missing are the
  // accountability cases, spare is parked stock.
  installed: 'ok', needs_check: 'warn', in_repair: 'warn',
  damaged: 'danger', missing: 'danger', spare: 'neutral',
  // Priority / severity
  low: 'neutral', medium: 'info', high: 'warn', critical: 'danger',
  // Parking alert levels.  ``breakdown`` fires when a truck has sat in an
  // unclassifiable spot past the breakdown threshold — a suspected
  // mechanical failure, not merely bad parking.  It carried a bespoke
  // purple at its call-site to mark it as a different KIND of event; that
  // is the job of the label text ("BREAKDOWN" vs "CRITICAL"), while colour
  // stays a pure severity channel across the whole app.  Both demand
  // immediate action, so both are danger.
  breakdown: 'danger',
  // Generic health
  healthy: 'ok', ok: 'ok', warning: 'warn', degraded: 'warn',
  // Telematics integration lifecycle.  Mirrors the
  // account_integrations.status column values; connected reads as a
  // healthy green pill, paused / disabled reads as warn (operator
  // intent — still configured), error reads as urgent, disconnected
  // reads as neutral (no signal, by design).
  connected: 'ok', paused: 'warn', disabled: 'warn',
  disconnected: 'neutral',
  coming_soon: 'neutral',
  // Driver-application pipeline: submitted=info (new, untriaged) →
  // screening/interview=warn (in progress) → approved/hired=ok →
  // rejected=danger, withdrawn=neutral.
  submitted: 'info', screening: 'warn', interview: 'warn',
  approved: 'ok', hired: 'ok', rejected: 'danger', withdrawn: 'neutral',
};

export function statusTone(status: string | null | undefined): Tone {
  if (!status) return 'neutral';
  return STATUS_TONE[String(status).toLowerCase().trim()] ?? 'neutral';
}

/** Convenience: status string → soft-pill classes in one call. */
export function statusClasses(
  status: string | null | undefined,
  opts?: { border?: boolean },
): string {
  return toneClasses(statusTone(status), opts);
}

/**
 * Chart series colours — reference the `--chart-1..5` tokens already in
 * index.css instead of hardcoding `#22c55e` etc. in Recharts props.
 * Pass the CSS var through to `fill` / `stroke`.  Index is 1-based to
 * match the token names; wraps after 5.
 *
 * The clamp is load-bearing, not defensive tidying.  JS `%` keeps the
 * sign of the dividend, so a 0-based caller got `((0 - 1) % 5) + 1 === 0`
 * and this returned `var(--chart-0)` — a token that exists in no theme,
 * so the mark rendered with NO colour at all.  That shipped: PartDetail's
 * cost line called `chartColor(0)`.  Wrapping the index instead of
 * trusting the caller means a 0-based or negative series index lands on
 * a real token rather than silently disappearing.
 */
export function chartColor(n: number): string {
  const i = Number.isFinite(n) ? ((Math.trunc(n) - 1) % 5 + 5) % 5 + 1 : 1;
  return `var(--chart-${i})`;
}
