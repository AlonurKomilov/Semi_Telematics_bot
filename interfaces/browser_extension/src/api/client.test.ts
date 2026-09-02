import { describe, it, expect, vi } from 'vitest';
import { apiFetch, clearToken, getToken, setToken, tokenExpiryMs, UnauthorizedError } from './client';

const jwt = (exp: number) => `h.${btoa(JSON.stringify({ exp })).replace(/=+$/, '')}.s`;

describe('extension API client', () => {
  it('keeps the token in extension storage, not the page', async () => {
    expect(await getToken()).toBeNull();
    await setToken('abc');
    expect(await getToken()).toBe('abc');
    await clearToken();
    expect(await getToken()).toBeNull();
  });
  it('reads exp off the token', () => {
    expect(tokenExpiryMs(jwt(1_700_000_000))).toBe(1_700_000_000_000);
    expect(tokenExpiryMs('garbage')).toBeNull();
  });
  it('sends the Bearer header and drops the token on 401', async () => {
    await setToken('tok');
    const fetchMock = vi.fn().mockResolvedValue({ status: 401, ok: false });
    vi.stubGlobal('fetch', fetchMock);
    await expect(apiFetch('/map/vehicles')).rejects.toBeInstanceOf(UnauthorizedError);
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer tok');
    expect(await getToken()).toBeNull();
    vi.unstubAllGlobals();
  });
});
