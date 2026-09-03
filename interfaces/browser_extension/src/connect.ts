/**
 * How the panel gets its token without ever seeing a password.
 *
 * 1. The panel makes a one-time ``state`` (32 random bytes), keeps it in
 *    ``chrome.storage.session`` for ten minutes, and opens the consent page
 *    on the APEX (4truck.us) — the one host every role knows; the apex
 *    signs the person in if needed and forwards to their role's host with
 *    the page and state intact.
 * 2. The person confirms THERE — signed in on 4truck.us, URL bar visible.
 * 3. The page hands the freshly minted, live-map-scoped token to the
 *    extension with ``chrome.runtime.sendMessage``; the service worker
 *    accepts it only when the sender is a 4truck origin, the ``state``
 *    is the pending one, and the token really is an extension token.
 *
 * Nothing here can start a connection by itself, and a page that was not
 * opened by this panel has no ``state`` to present.
 */
export const CONNECT_STATE_KEY = 'connectState';
export const CONNECT_TTL_MS = 10 * 60_000;
export const DASHBOARD_BASE =
  (import.meta.env.VITE_DASHBOARD_BASE as string | undefined) ?? 'https://4truck.us';

export interface PendingConnect { state: string; expires: number }

export type ConnectMessage =
  | { type: '4truck:ping'; state?: unknown }
  | { type: '4truck:connect'; state?: unknown; token?: unknown };

export type Verdict = { ok: true; token: string } | { ok: false; reason: string };

/** Only a 4truck page may talk to the extension — and only over https. */
export function isTrustedOrigin(origin: string | undefined): boolean {
  return !!origin && /^https:\/\/([a-z0-9-]+\.)?4truck\.us$/.test(origin);
}

export function newState(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

/** The token must be the scoped kind — a full dashboard token pushed at
 *  the extension is refused, whoever sent it. */
export function isExtensionToken(token: unknown): token is string {
  if (typeof token !== 'string' || !token) return false;
  try {
    const [, payload] = token.split('.');
    const json = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')));
    return json.aud === 'extension' && Array.isArray(json.scope) && json.scope.includes('can_location_map');
  } catch {
    return false;
  }
}

export function statePending(pending: PendingConnect | null, state: unknown, now = Date.now()): boolean {
  return !!pending && typeof state === 'string' && state === pending.state && now < pending.expires;
}

export function acceptConnectMessage(
  msg: unknown, senderOrigin: string | undefined, pending: PendingConnect | null, now = Date.now(),
): Verdict {
  if (!isTrustedOrigin(senderOrigin)) return { ok: false, reason: 'origin' };
  const m = msg as ConnectMessage | null;
  if (!m || m.type !== '4truck:connect') return { ok: false, reason: 'type' };
  if (!statePending(pending, m.state, now)) return { ok: false, reason: 'state' };
  if (!isExtensionToken(m.token)) return { ok: false, reason: 'token' };
  return { ok: true, token: m.token };
}

export async function getPending(): Promise<PendingConnect | null> {
  const got = await chrome.storage.session.get(CONNECT_STATE_KEY);
  const v = got[CONNECT_STATE_KEY] as PendingConnect | undefined;
  return v && typeof v.state === 'string' && typeof v.expires === 'number' ? v : null;
}

export async function clearPending(): Promise<void> {
  await chrome.storage.session.remove(CONNECT_STATE_KEY);
}

/** Start a connection: remember a fresh state, open the consent page. */
export async function beginConnect(): Promise<void> {
  const state = newState();
  await chrome.storage.session.set({ [CONNECT_STATE_KEY]: { state, expires: Date.now() + CONNECT_TTL_MS } });
  await chrome.tabs.create({ url: `${DASHBOARD_BASE}/extension/connect?state=${state}` });
}
