import { describe, it, expect } from 'vitest';
import { DASHBOARD_BASE, REGISTER_URL, acceptConnectMessage, isExtensionToken, isTrustedOrigin, newState, statePending, type PendingConnect } from './connect';

function jwt(payload: Record<string, unknown>): string {
  const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/=+$/, '');
  return `${b64({ alg: 'HS256' })}.${b64(payload)}.sig`;
}
// Every name the server has used for the live-map scope, so the vector
// is the real token under the verb migration and before it alike.
const EXT = jwt({ aud: 'extension', scope: ['can_view_location', 'can_location_map', 'can_location_vehicle'], sub: '1' });
const FULL = jwt({ sub: '1', role: 'owner' });
const pending: PendingConnect = { state: 'a'.repeat(64), expires: 2_000 };

describe('who may hand the panel a token', () => {
  it('only a 4truck page, over https', () => {
    expect(isTrustedOrigin('https://dash.4truck.us')).toBe(true);
    expect(isTrustedOrigin('https://fleet.4truck.us')).toBe(true);
    expect(isTrustedOrigin('https://4truck.us')).toBe(true);
    expect(isTrustedOrigin('http://dash.4truck.us')).toBe(false);
    expect(isTrustedOrigin('https://4truck.us.evil.com')).toBe(false);
    expect(isTrustedOrigin('https://evil4truck.us')).toBe(false);
    expect(isTrustedOrigin(undefined)).toBe(false);
  });
  it('only with the state this panel generated, while it is fresh', () => {
    expect(statePending(pending, pending.state, 1_000)).toBe(true);
    expect(statePending(pending, pending.state, 2_000)).toBe(false);   // expired
    expect(statePending(pending, 'b'.repeat(64), 1_000)).toBe(false);  // someone else's
    expect(statePending(null, pending.state, 1_000)).toBe(false);      // nothing pending
  });
  it('only a live-map-scoped token — a full dashboard token is refused', () => {
    expect(isExtensionToken(EXT)).toBe(true);
    expect(isExtensionToken(FULL)).toBe(false);
    expect(isExtensionToken('not-a-jwt')).toBe(false);
    expect(isExtensionToken(undefined)).toBe(false);
  });
  it('all three together, in order, and the reason names the first failure', () => {
    const good = { type: '4truck:connect', state: pending.state, token: EXT };
    expect(acceptConnectMessage(good, 'https://dash.4truck.us', pending, 1_000)).toEqual({ ok: true, token: EXT });
    expect(acceptConnectMessage(good, 'https://evil.com', pending, 1_000)).toEqual({ ok: false, reason: 'origin' });
    expect(acceptConnectMessage({ ...good, type: '4truck:ping' }, 'https://dash.4truck.us', pending, 1_000)).toEqual({ ok: false, reason: 'type' });
    expect(acceptConnectMessage({ ...good, state: 'x' }, 'https://dash.4truck.us', pending, 1_000)).toEqual({ ok: false, reason: 'state' });
    expect(acceptConnectMessage({ ...good, token: FULL }, 'https://dash.4truck.us', pending, 1_000)).toEqual({ ok: false, reason: 'token' });
  });
  it('states are 32 random bytes as hex and never repeat', () => {
    const a = newState(), b = newState();
    expect(a).toMatch(/^[0-9a-f]{64}$/);
    expect(a).not.toBe(b);
  });
});

describe('the register door', () => {
  it('is on the same site as the consent page, at its Register tab — the panel holds no form for it', () => {
    expect(REGISTER_URL.startsWith(`${DASHBOARD_BASE}/`)).toBe(true);
    expect(new URL(REGISTER_URL).searchParams.get('mode')).toBe('register');
  });
});
