/**
 * The engine resolver, and the one rule under every branch of it: a
 * carrier keeps a working map.
 *
 * Google can refuse for reasons that have nothing to do with the
 * person looking at the screen — a key the account was never given, a
 * referrer restriction that does not cover this host, a daily quota
 * spent by lunchtime, a blocked network. Each of those must end at
 * OpenStreetMap with a reason recorded, never at a blank rectangle.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

import { useMapEngine } from './useMapEngine';

const apiJSON = vi.hoisted(() => vi.fn());
vi.mock('../../../api/client', () => ({ apiJSON }));

const loader = vi.hoisted(() => ({ load: vi.fn(), loaded: vi.fn() }));
vi.mock('./googleLoader', async (orig) => {
  const real = await orig<typeof import('./googleLoader')>();
  return { ...real, loadGoogleMaps: loader.load, isGoogleLoaded: loader.loaded };
});

beforeEach(() => {
  apiJSON.mockReset();
  loader.load.mockReset().mockResolvedValue({ maps: {} });
  loader.loaded.mockReset().mockReturnValue(true);
});

async function engineFor(wire: unknown) {
  apiJSON.mockResolvedValue(wire);
  const { result } = renderHook(() => useMapEngine());
  await waitFor(() => expect(result.current.loading).toBe(false));
  return result.current;
}

describe('which engine a surface draws', () => {
  it('starts as loading, so a skeleton is not mistaken for a chosen engine', () => {
    apiJSON.mockReturnValue(new Promise(() => {}));   // never settles
    const { result } = renderHook(() => useMapEngine());
    expect(result.current.loading).toBe(true);
    expect(result.current.engine).toBeNull();
  });

  it('draws the free engine when the account has not bought the other', async () => {
    const s = await engineFor({ engine: 'osm', requested: 'osm', engines: ['osm', 'google'], google_available: false });
    expect(s.engine).toBe('osm');
    expect(s.fellBackFrom).toBeNull();
    expect(loader.load).not.toHaveBeenCalled();       // no billable script fetched
  });

  it('draws Google once its script is really loaded, not when the answer arrives', async () => {
    const s = await engineFor({ engine: 'google', requested: 'google', engines: ['osm', 'google'], google_available: true, key: 'AIza-test' });
    expect(s.engine).toBe('google');
    expect(loader.load).toHaveBeenCalledWith('AIza-test');
  });
});

describe('every way Google can refuse ends at a working map', () => {
  it('the server chose Google but the platform has no key', async () => {
    const s = await engineFor({ engine: 'osm', requested: 'google', engines: ['osm', 'google'], google_available: false });
    expect(s.engine).toBe('osm');
    expect(s.fellBackFrom).toBe('google');
    expect(s.reason).toMatch(/no Google Maps key/i);
  });

  it('the answer named Google and carried no key', async () => {
    const s = await engineFor({ engine: 'google', requested: 'google', engines: ['osm', 'google'], google_available: true });
    expect(s.engine).toBe('osm');
    expect(s.fellBackFrom).toBe('google');
  });

  it('the script could not be fetched — a refused key, a referrer, a blocked network', async () => {
    loader.load.mockRejectedValue(new Error('Could not reach the Google Maps script.'));
    const s = await engineFor({ engine: 'google', requested: 'google', engines: ['osm', 'google'], google_available: true, key: 'AIza-test' });
    expect(s.engine).toBe('osm');
    expect(s.fellBackFrom).toBe('google');
    expect(s.reason).toMatch(/did not load/i);
  });

  it('the script loaded but installed nothing — the half-load Google warns about', async () => {
    loader.loaded.mockReturnValue(false);
    const s = await engineFor({ engine: 'google', requested: 'google', engines: ['osm', 'google'], google_available: true, key: 'AIza-test' });
    expect(s.engine).toBe('osm');
  });

  it('the endpoint itself is unreachable', async () => {
    apiJSON.mockRejectedValue(new Error('offline'));
    const { result } = renderHook(() => useMapEngine());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.engine).toBe('osm');
  });
});
