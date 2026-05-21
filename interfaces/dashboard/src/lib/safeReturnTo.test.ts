import { describe, it, expect } from 'vitest';
import { isSafeReturnTo } from './safeReturnTo';

/**
 * Regression guard for the open-redirect vulnerability in the
 * post-login ``?return_to=…`` handler.  An earlier substring check
 * (``returnTo.includes('4truck.us')``) accepted attacker-controlled
 * URLs that merely contained the literal apex anywhere — host
 * (``4truck.us.attacker.com``), path, or query.  These tests pin the
 * positive AND negative cases against the parsed-URL implementation
 * so any future drift fails CI.
 *
 * Apex is passed explicitly to avoid depending on the
 * ``VITE_APEX_DOMAIN`` env var at test time.
 */
const APEX = '4truck.us';

describe('isSafeReturnTo — accepts only our own origin', () => {
  it('accepts the apex itself', () => {
    expect(isSafeReturnTo('https://4truck.us/', APEX)).toBe(true);
    expect(isSafeReturnTo('https://4truck.us/admin/storage', APEX)).toBe(true);
  });

  it('accepts direct subdomains of the apex', () => {
    expect(isSafeReturnTo('https://dash.4truck.us/', APEX)).toBe(true);
    expect(isSafeReturnTo('https://fleet.4truck.us/vehicles', APEX)).toBe(true);
    expect(isSafeReturnTo('https://dispatch.4truck.us/routes', APEX)).toBe(true);
    expect(isSafeReturnTo('https://safety.4truck.us/scorecards', APEX)).toBe(true);
    expect(isSafeReturnTo('https://api.4truck.us/api/something', APEX)).toBe(true);
  });

  it('accepts nested subdomains as long as the apex suffix matches', () => {
    expect(isSafeReturnTo('https://preview.dash.4truck.us/', APEX)).toBe(true);
  });
});

describe('isSafeReturnTo — rejects open-redirect payloads', () => {
  it('rejects attacker host that uses the apex as a fake subdomain', () => {
    // Classic open-redirect pattern: literal apex appears in URL but
    // the actual host is attacker-controlled.
    expect(isSafeReturnTo('https://4truck.us.attacker.com/', APEX)).toBe(false);
    expect(isSafeReturnTo('https://4truck.us.evil.net/login', APEX)).toBe(false);
  });

  it('rejects attacker host with apex appearing only in path / query', () => {
    expect(isSafeReturnTo('https://attacker.com/?marker=4truck.us', APEX)).toBe(false);
    expect(isSafeReturnTo('https://attacker.com/4truck.us/login', APEX)).toBe(false);
    expect(isSafeReturnTo('https://attacker.com/path?x=https://4truck.us', APEX)).toBe(false);
  });

  it('rejects look-alike hosts that share a suffix without a leading dot', () => {
    // ``my4truck.us`` ends with ``4truck.us`` but is NOT a subdomain
    // — the leading dot in the suffix check guards against this.
    expect(isSafeReturnTo('https://my4truck.us/', APEX)).toBe(false);
    expect(isSafeReturnTo('https://foo4truck.us/login', APEX)).toBe(false);
  });

  it('rejects unsafe schemes', () => {
    expect(isSafeReturnTo('http://4truck.us/', APEX)).toBe(false);          // plain HTTP
    expect(isSafeReturnTo('javascript:alert(1)', APEX)).toBe(false);
    expect(isSafeReturnTo('data:text/html,<script>alert(1)</script>', APEX)).toBe(false);
    expect(isSafeReturnTo('ftp://4truck.us/', APEX)).toBe(false);
    // Protocol-relative — leaves origin ambiguous, must be rejected.
    expect(isSafeReturnTo('//4truck.us/', APEX)).toBe(false);
    expect(isSafeReturnTo('//attacker.com/', APEX)).toBe(false);
  });

  it('rejects malformed input', () => {
    expect(isSafeReturnTo(null, APEX)).toBe(false);
    expect(isSafeReturnTo(undefined, APEX)).toBe(false);
    expect(isSafeReturnTo('', APEX)).toBe(false);
    expect(isSafeReturnTo('not a url', APEX)).toBe(false);
    expect(isSafeReturnTo('https://', APEX)).toBe(false);  // no host
  });

  it('host matching is case-insensitive', () => {
    expect(isSafeReturnTo('https://DASH.4TRUCK.US/', APEX)).toBe(true);
    expect(isSafeReturnTo('https://4truck.us.ATTACKER.com/', APEX)).toBe(false);
  });
});
