/**
 * The script tag, and the three ways loading it goes wrong.
 *
 * Its own file because the resolver's tests MOCK this module — a mock
 * that reached these would test the mock.  The Maps JavaScript API is
 * a global-installing script, so the things worth pinning are all
 * about the page: one tag however many maps ask, a pinned version, and
 * a failure that leaves nothing behind for the next attempt.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import { loadGoogleMaps, isGoogleLoaded, resetGoogleLoaderForTests } from './googleLoader';

describe('the Google Maps script loader', () => {
  beforeEach(() => { resetGoogleLoaderForTests(); delete (window as { google?: unknown }).google; });
  afterEach(() => { resetGoogleLoaderForTests(); });

  it('refuses an empty key rather than fetching a script that cannot work', async () => {
    await expect(loadGoogleMaps('')).rejects.toThrow(/no google maps key/i);
  });

  it('asks Google for the script once however many surfaces want a map', async () => {
    const a = loadGoogleMaps('AIza-1');
    const b = loadGoogleMaps('AIza-1');
    expect(document.querySelectorAll('script[src*="maps.googleapis.com"]').length).toBe(1);
    (window as { google?: unknown }).google = { maps: {} };
    (window as unknown as Record<string, () => void>).__4truckGoogleMapsReady();
    await expect(a).resolves.toBeTruthy();
    await expect(b).resolves.toBeTruthy();
    expect(isGoogleLoaded()).toBe(true);
  });

  it('passes the key and pins a version, so a Google rollout cannot change the map under us', () => {
    void loadGoogleMaps('AIza-2');
    const src = document.querySelector<HTMLScriptElement>('script[src*="maps.googleapis.com"]')!.src;
    expect(src).toContain('key=AIza-2');
    expect(src).toMatch(/[?&]v=/);
    expect(src).toContain('callback=__4truckGoogleMapsReady');
  });

  it('a failed fetch rejects and leaves no global behind for the next try', async () => {
    const p = loadGoogleMaps('AIza-3');
    document.querySelector<HTMLScriptElement>('script[src*="maps.googleapis.com"]')!
      .dispatchEvent(new Event('error'));
    await expect(p).rejects.toThrow(/could not reach/i);
    expect((window as unknown as Record<string, unknown>).__4truckGoogleMapsReady).toBeUndefined();
  });
});
