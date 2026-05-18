/**
 * Single-source date/time formatters for the dashboard.
 *
 * Every "show this timestamp to the user" call site should go through
 * one of these helpers so the user's effective timezone (resolved by
 * the API as ``user.effective_timezone`` on ``/user/me``) is honored
 * consistently — instead of falling back to whatever locale the
 * browser happens to be in.
 *
 * Callers that don't have a tz handy can pass ``undefined`` and we
 * fall back to the browser default, preserving current behavior at
 * unmigrated sites.
 */

export interface FormatDateOptions {
  /** Override the auto-detected timezone (IANA name).  Falls back to
   *  the browser locale when omitted. */
  timeZone?: string;
  /** Pass-through for Intl.DateTimeFormat options when the defaults
   *  ("MMM dd, yyyy HH:mm") aren't right (e.g. date-only). */
  intl?: Intl.DateTimeFormatOptions;
}

function _toDate(value: string | number | Date | null | undefined): Date | null {
  if (value == null || value === '') return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Format a timestamp as "Sep 14, 2026, 03:42 PM" in the given tz. */
export function formatDate(
  value: string | number | Date | null | undefined,
  opts: FormatDateOptions = {},
): string {
  const d = _toDate(value);
  if (!d) return '—';
  const intl: Intl.DateTimeFormatOptions = {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
    timeZone: opts.timeZone,
    ...(opts.intl ?? {}),
  };
  try {
    return new Intl.DateTimeFormat(undefined, intl).format(d);
  } catch {
    // Bad tz string — fall back to local without timeZone.
    const { timeZone: _ignored, ...rest } = intl;
    return new Intl.DateTimeFormat(undefined, rest).format(d);
  }
}

/** Date-only variant — "Sep 14, 2026". */
export function formatDay(
  value: string | number | Date | null | undefined,
  opts: FormatDateOptions = {},
): string {
  return formatDate(value, {
    timeZone: opts.timeZone,
    intl: { year: 'numeric', month: 'short', day: '2-digit', ...(opts.intl ?? {}) },
  });
}

/** Time-only variant — "03:42 PM". */
export function formatTime(
  value: string | number | Date | null | undefined,
  opts: FormatDateOptions = {},
): string {
  return formatDate(value, {
    timeZone: opts.timeZone,
    intl: { hour: '2-digit', minute: '2-digit', ...(opts.intl ?? {}) },
  });
}
