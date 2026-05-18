/**
 * Resolve the timezone every formatter / date-picker in the dashboard
 * should use.
 *
 * Source of truth — in order:
 *   1. `user.effective_timezone` from /user/me (already user override
 *      → account default → fallback merged server-side).
 *   2. Browser locale, via `Intl.DateTimeFormat().resolvedOptions().timeZone`.
 *
 * Returning the browser tz as a last resort keeps the dashboard
 * usable on shared kiosks where no user is logged in or the API
 * doesn't carry the field yet.
 */

import { useAuth } from '../context/AuthContext';

export function useTimezone(): string {
  const { user } = useAuth();
  const fromUser = user?.effective_timezone || user?.timezone;
  if (fromUser) return fromUser;
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'America/New_York';
  } catch {
    return 'America/New_York';
  }
}
