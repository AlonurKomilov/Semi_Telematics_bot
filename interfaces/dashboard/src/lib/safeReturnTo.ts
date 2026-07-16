/**
 * Origin guard for the ``?return_to=…`` post-login redirect.
 *
 * The login flow forwards the user to whatever URL the ``return_to``
 * query param holds — useful when an unauthenticated request is
 * bounced to the apex and we want the user to land back on the page
 * they originally tried to open.  Without validation, an attacker can
 * weaponise that query param to phish freshly-authenticated users
 * (open-redirect chain).
 *
 * This module is the SINGLE source of truth for "is this URL one of
 * ours?" — both ``AuthContext`` (post-login redirect) and ``App``
 * (apex unauth bounce) call ``isSafeReturnTo`` so the two paths can't
 * drift.  An earlier implementation in ``AuthContext`` used a
 * substring match (``returnTo.includes('4truck.us')``) which accepted
 * URLs like ``https://4truck.us.attacker.com/`` or
 * ``https://attacker.com/?x=4truck.us`` and was flagged by the
 * security review.
 *
 * Acceptance rules (all required):
 *   1. Parses as a valid URL via the WHATWG ``URL`` constructor.
 *   2. Scheme is exactly ``https:`` — never plain HTTP, never
 *      ``javascript:`` / ``data:`` / other.
 *   3. Hostname matches the apex exactly OR is a direct subdomain
 *      (``host === apex`` || ``host.endsWith('.' + apex)``).  The
 *      leading dot in the suffix check is critical — without it
 *      ``my4truck.us`` would match against an apex of ``4truck.us``.
 */

export const APEX_DOMAIN =
  (import.meta.env.VITE_APEX_DOMAIN as string | undefined) ?? '4truck.us';

/**
 * Explicit sign-out marker — set by ``AuthContext.logout`` right before
 * it clears the token, checked by ``App``'s unauth bounce.
 *
 * Why: once the token is cleared, any in-flight request 401s, the user
 * state nulls, and App's bounce navigates away — BEFORE the server has
 * processed the logout POST.  The apex then still sees a live cookie,
 * auto-forwards back to the dashboard, which by now rejects… a visible
 * multi-hop loop between domains until the revoke lands.  While this
 * marker is active the bounce makes NO navigation at all: ``logout()``
 * owns the single navigation, which it performs strictly AFTER the
 * server confirmed the session is dead — so the apex probe can never
 * race the revoke.
 *
 * The marker is a TIMESTAMP with a short validity window (not a
 * consumed boolean): the bounce may fire several times during one
 * sign-out and must stand down every time, yet a stale flag from a
 * dead sign-out attempt must never suppress a legitimate bounce later
 * (that would strand the tab on a spinner).  sessionStorage is
 * per-tab, which is exactly right: only the sign-out tab stands down.
 */
export const EXPLICIT_SIGNOUT_KEY = 'explicit_signout_at';
const SIGNOUT_WINDOW_MS = 15_000;

export function markExplicitSignout(): void {
  try {
    sessionStorage.setItem(EXPLICIT_SIGNOUT_KEY, String(Date.now()));
  } catch { /* private mode */ }
}

export function clearExplicitSignout(): void {
  try {
    sessionStorage.removeItem(EXPLICIT_SIGNOUT_KEY);
  } catch { /* private mode */ }
}

/** True while this tab is inside the sign-out window; expired flags
 *  are cleaned up and ignored. */
export function explicitSignoutActive(now: number = Date.now()): boolean {
  try {
    const raw = sessionStorage.getItem(EXPLICIT_SIGNOUT_KEY);
    if (!raw) return false;
    const ts = Number(raw);
    if (!Number.isFinite(ts) || now - ts > SIGNOUT_WINDOW_MS) {
      sessionStorage.removeItem(EXPLICIT_SIGNOUT_KEY);
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

export function isSafeReturnTo(raw: string | null | undefined, apex: string = APEX_DOMAIN): boolean {
  if (!raw) return false;
  if (!raw.startsWith('https://')) return false;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false;
  }
  if (url.protocol !== 'https:') return false;
  const host = url.hostname.toLowerCase();
  const lowerApex = apex.toLowerCase();
  return host === lowerApex || host.endsWith(`.${lowerApex}`);
}
