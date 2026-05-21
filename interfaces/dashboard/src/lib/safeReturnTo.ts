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
