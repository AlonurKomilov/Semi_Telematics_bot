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

export type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral';

const TONE_CLASSES: Record<Tone, string> = {
  ok:      'bg-ok-bg text-ok border-ok-bd',
  warn:    'bg-warn-bg text-warn border-warn-bd',
  danger:  'bg-danger-bg text-danger border-danger-bd',
  info:    'bg-info-bg text-info border-info-bd',
  neutral: 'bg-muted text-muted-foreground border-border',
};

/** Soft-pill class recipe for a tone (bg /15, solid text, border /30). */
export function toneClasses(tone: Tone): string {
  return TONE_CLASSES[tone];
}

/** Just the solid foreground colour class — for icons, dots, chart bars. */
export function toneText(tone: Tone): string {
  return tone === 'neutral' ? 'text-muted-foreground' : `text-${tone}`;
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
  // Registry vehicle with no telematics match (trailer / not-yet-
  // equipped truck) — neutral, not red: it's a deliberate state, not
  // a fault.
  no_telemetry: 'neutral',
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
  // Priority / severity
  low: 'neutral', medium: 'info', high: 'warn', critical: 'danger',
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
export function statusClasses(status: string | null | undefined): string {
  return toneClasses(statusTone(status));
}

/**
 * Chart series colours — reference the `--chart-1..5` tokens already in
 * index.css instead of hardcoding `#22c55e` etc. in Recharts props.
 * Pass the CSS var through to `fill` / `stroke`.  Index is 1-based to
 * match the token names; wraps after 5.
 */
export function chartColor(n: number): string {
  const i = ((n - 1) % 5) + 1;
  return `var(--chart-${i})`;
}
