/**
 * The engine resolver, and the one rule under every branch of it: a
 * carrier keeps a working map.
 *
 * Google can refuse for reasons that have nothing to do with the
 * person looking at the screen — a key the platform was never given,
 * a daily quota spent by lunchtime, a session it would not open, a
 * blocked network. Each must end at OpenStreetMap with a reason
 * recorded, never at a blank rectangle.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

import { providerFromWire, useMapEngine, type EngineWire } from './useMapEngine';

const apiJSON = vi.hoisted(() => vi.fn());
vi.mock('../../../api/client', () => ({ apiJSON }));

beforeEach(() => { apiJSON.mockReset(); });

const wire = (over: Partial<EngineWire> = {}): EngineWire => ({
  engine: 'osm', requested: 'osm', engines: ['osm', 'google'], google_available: false, ...over,
});

async function settled(w: unknown) {
  apiJSON.mockResolvedValue(w);
  const hook = renderHook(() => useMapEngine());
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  return hook;
}

describe('which basemap a surface draws', () => {
  it('starts as loading, so a skeleton is not mistaken for a chosen engine', () => {
    apiJSON.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useMapEngine());
    expect(result.current.loading).toBe(true);
    expect(result.current.engine).toBeNull();
  });

  it('draws the free engine when the account has not bought the other', async () => {
    const { result } = await settled(wire());
    expect(result.current.engine).toBe('osm');
    expect(result.current.fellBackFrom).toBeNull();
    expect(result.current.googleAvailable).toBe(false);
  });

  it('draws Google when the server resolved to it — tiles, so nothing to load', async () => {
    const { result } = await settled(wire({ engine: 'google', requested: 'google', google_available: true }));
    expect(result.current.engine).toBe('google');
    expect(apiJSON).toHaveBeenCalledTimes(1);          // no script, no session yet
  });

  it('the account asked for Google and the platform has no key', async () => {
    const { result } = await settled(wire({ requested: 'google' }));
    expect(result.current.engine).toBe('osm');
    expect(result.current.fellBackFrom).toBe('google');
    expect(result.current.reason).toMatch(/no Google Maps key/i);
  });

  it('the endpoint itself is unreachable', async () => {
    apiJSON.mockRejectedValue(new Error('offline'));
    const { result } = renderHook(() => useMapEngine());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.engine).toBe('osm');
  });
});

describe('after resolution', () => {
  it('a tile session is asked of the server per type', async () => {
    const { result } = await settled(wire({ engine: 'google', requested: 'google', google_available: true }));
    apiJSON.mockResolvedValueOnce({ type: 'satellite', tile_url: 'u', viewport_url: 'v', tile_size: 256, image_format: 'jpeg', expiry: 1, max_zoom: 22 });
    const s = await result.current.tileSession('satellite');
    expect(apiJSON).toHaveBeenLastCalledWith('/map/tiles/session?type=satellite');
    expect(s.tile_url).toBe('u');
  });

  it('a Google failure after resolution drops to OSM and keeps the reason', async () => {
    const { result } = await settled(wire({ engine: 'google', requested: 'google', google_available: true }));
    act(() => result.current.fallBack('Google refused the session (quota).'));
    expect(result.current.engine).toBe('osm');
    expect(result.current.fellBackFrom).toBe('google');
    expect(result.current.reason).toMatch(/quota/);
  });

  it('falling back when already on OSM changes nothing', async () => {
    const { result } = await settled(wire());
    const before = result.current;
    act(() => result.current.fallBack('x'));
    expect(result.current.engine).toBe('osm');
    expect(result.current.reason).toBe(before.reason);
  });

  it('refresh asks the server again — after the setting changed', async () => {
    const { result } = await settled(wire());
    apiJSON.mockResolvedValue(wire({ engine: 'google', requested: 'google', google_available: true }));
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.engine).toBe('google'));
  });
});

describe('providerFromWire', () => {
  it('never reports google unless the server resolved to it', () => {
    expect(providerFromWire(wire({ requested: 'google', google_available: true })).engine).toBe('osm');
  });
});
