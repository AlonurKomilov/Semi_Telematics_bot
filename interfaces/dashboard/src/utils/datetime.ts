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

/** Today's calendar date as ``YYYY-MM-DD`` in the given timezone.
 *
 *  The single correct way to ask "what day is it for this account?" —
 *  ``new Date().toISOString().slice(0,10)`` answers in UTC, which flips
 *  the date a day early/late near midnight.  ``en-CA`` formats as
 *  ``YYYY-MM-DD``.  Omitting ``tz`` falls back to the browser locale. */
export function todayInTimeZone(tz?: string): string {
  try {
    return new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(new Date());
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
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

/** Date-only variant — "Sep 14, 2026".
 *
 *  ``hour``/``minute`` are set to ``undefined`` to override ``formatDate``'s
 *  date+time defaults (Intl ignores undefined options) — otherwise this
 *  would leak the time, e.g. "Sep 14, 2026, 11:42 AM". */
export function formatDay(
  value: string | number | Date | null | undefined,
  opts: FormatDateOptions = {},
): string {
  return formatDate(value, {
    timeZone: opts.timeZone,
    intl: {
      year: 'numeric', month: 'short', day: '2-digit',
      hour: undefined, minute: undefined,
      ...(opts.intl ?? {}),
    },
  });
}

/** Time-only variant — "03:42 PM".
 *
 *  ``year``/``month``/``day`` set to ``undefined`` so this doesn't inherit
 *  ``formatDate``'s date defaults and leak the date. */
export function formatTime(
  value: string | number | Date | null | undefined,
  opts: FormatDateOptions = {},
): string {
  return formatDate(value, {
    timeZone: opts.timeZone,
    intl: {
      year: undefined, month: undefined, day: undefined,
      hour: '2-digit', minute: '2-digit',
      ...(opts.intl ?? {}),
    },
  });
}


/** Relative-time variant — "5m ago", "2h ago", "3d ago", "yesterday".
 *
 *  Falls back to the absolute date for anything older than ~30 days so
 *  the user always sees a concrete reference for older items.  Use this
 *  on lists where freshness matters more than the exact timestamp
 *  (knowledge base cards, login activity, etc.).
 */
export function formatRelative(
  value: string | number | Date | null | undefined,
  opts: FormatDateOptions = {},
): string {
  const d = _toDate(value);
  if (!d) return '—';
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 5)       return 'just now';
  if (sec < 60)      return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60)      return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24)       return `${hr}h ago`;
  const days = Math.floor(hr / 24);
  if (days === 1)    return 'yesterday';
  if (days < 30)     return `${days}d ago`;
  return formatDay(d, opts);
}
