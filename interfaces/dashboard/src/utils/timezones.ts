/**
 * Friendly timezone labels for every IANA name the backend accepts.
 *
 * Deliberately limited to the **four contiguous-US zones** — that's
 * the entire customer surface today (US trucking fleets).  Keeping
 * the dropdown to four options removes a class of misconfiguration
 * where an admin picks ``UTC`` "because it sounds technical" and
 * then gets every alert at the wrong hour.
 *
 * Re-add Phoenix / Alaska / Hawaii / UTC here (and in the backend
 * whitelist ``capabilities/localization/tz.IANA_OPTIONS``) when a
 * customer outside the contiguous US onboards.
 *
 * Why "Eastern Time (ET)" instead of "EST":
 *   The familiar abbreviations (EST / CST / MST / PST) shift between
 *   standard and daylight (EST↔EDT, CST↔CDT, …) — showing just
 *   "EST" is wrong six months a year.  ``ET``/``CT``/``MT``/``PT``
 *   are the zone-neutral shorthand that stay correct year-round.
 */

export interface TimezoneOption {
  /** IANA name — what we send to the backend and store in the DB. */
  value: string;
  /** Two-letter shorthand (year-round; not the EST/EDT split). */
  abbrev: string;
  /** Human-readable zone name (e.g. "Eastern Time"). */
  label: string;
  /** City suffix shown after the label so admins can sanity-check
   *  which IANA name they're picking. */
  city: string;
}

export const TIMEZONE_OPTIONS: TimezoneOption[] = [
  { value: 'America/Los_Angeles', abbrev: 'PT', label: 'Pacific Time',  city: 'Los Angeles' },
  { value: 'America/Denver',      abbrev: 'MT', label: 'Mountain Time', city: 'Denver' },
  { value: 'America/Chicago',     abbrev: 'CT', label: 'Central Time',  city: 'Chicago' },
  { value: 'America/New_York',    abbrev: 'ET', label: 'Eastern Time',  city: 'New York' },
];

const _BY_VALUE: Record<string, TimezoneOption> = Object.fromEntries(
  TIMEZONE_OPTIONS.map((o) => [o.value, o]),
);

/** Look up a friendly entry by IANA name; returns ``undefined`` for
 *  legacy / unknown values so callers can decide how to render the
 *  raw IANA fallback. */
export function timezoneOption(iana: string): TimezoneOption | undefined {
  return _BY_VALUE[iana];
}

/** Render an IANA name as ``"Eastern Time (ET) — New York"``,
 *  falling back to the raw value when we don't recognise it. */
export function timezoneLabel(iana: string): string {
  const o = _BY_VALUE[iana];
  if (!o) return iana;
  const suffix = o.city ? ` — ${o.city}` : '';
  return `${o.label} (${o.abbrev})${suffix}`;
}

/** Current wall-clock time in ``iana``, e.g. ``"3:42 AM"`` for
 *  ``"America/New_York"``.  Returns empty string for unknown zones
 *  so callers can fall back to the static label. */
export function currentTimeInTz(iana: string, now?: Date): string {
  const d = now ?? new Date();
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: iana,
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    }).format(d);
  } catch {
    return '';
  }
}

/** Like ``timezoneLabel`` but appends the *current* time in that zone
 *  instead of the example city — e.g. ``"Eastern Time (ET) — 3:42 AM"``.
 *  Useful in pickers so an admin can immediately see what each option
 *  means without converting "America/New_York" in their head. */
export function timezoneLabelWithTime(iana: string, now?: Date): string {
  const o = _BY_VALUE[iana];
  if (!o) return iana;
  const t = currentTimeInTz(iana, now);
  return t ? `${o.label} (${o.abbrev}) — ${t}` : `${o.label} (${o.abbrev})`;
}

/** Short chip-style label, e.g. ``"ET"`` for inline rendering when
 *  full label text doesn't fit (drawers, table cells). */
export function timezoneAbbrev(iana: string): string {
  return _BY_VALUE[iana]?.abbrev ?? iana;
}
