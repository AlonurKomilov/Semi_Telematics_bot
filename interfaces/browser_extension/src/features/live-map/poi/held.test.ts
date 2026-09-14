import { describe, it, expect } from 'vitest';
import { planFor, readHeld, writeHeld, dropHeld } from './held';
import type { PoiFeature } from './viewport';

const pt = (lat: number, lng: number, name: string): PoiFeature => ({
  type: 'Feature',
  geometry: { type: 'Point', coordinates: [lng, lat] },
  properties: { name },
});

describe('planFor', () => {
  it('holds when our version is the server version', () => {
    expect(planFor('2026-09-14T03:12:00Z', '2026-09-14T03:12:00Z')).toBe('hold');
  });

  it('downloads when we hold nothing', () => {
    expect(planFor(null, '2026-09-14T03:12:00Z')).toBe('download');
  });

  it('downloads when the server has re-imported since', () => {
    expect(planFor('2026-09-07T03:12:00Z', '2026-09-14T03:12:00Z')).toBe('download');
  });

  // The one that matters: a layer the server cannot hand over whole must
  // keep asking per viewport.  Drawing an empty layer instead would be a
  // failure wearing the shape of an answer — the map would say "there is
  // no fuel here", which is a different claim from "I could not ask".
  it('falls back to the viewport path when the server has no version', () => {
    expect(planFor(null, null)).toBe('per-view');
    expect(planFor('2026-09-14T03:12:00Z', null)).toBe('per-view');
    expect(planFor(null, undefined)).toBe('per-view');
  });
});

describe('the store', () => {
  it('gives back what it was given', async () => {
    const held = { version: 'v1', features: [pt(41.8, -87.6, 'Pilot')] };
    expect(await writeHeld('fuel_station', held)).toBe(true);
    expect(await readHeld('fuel_station')).toEqual(held);
  });

  it('keeps layers apart', async () => {
    await writeHeld('fuel_station', { version: 'v1', features: [pt(41.8, -87.6, 'Pilot')] });
    await writeHeld('shower', { version: 'v2', features: [] });
    expect((await readHeld('fuel_station'))?.version).toBe('v1');
    expect((await readHeld('shower'))?.version).toBe('v2');
  });

  it('answers null for a layer it never held', async () => {
    expect(await readHeld('rest_area')).toBeNull();
  });

  it('forgets on demand', async () => {
    await writeHeld('shower', { version: 'v1', features: [] });
    await dropHeld('shower');
    expect(await readHeld('shower')).toBeNull();
  });

  // Storage survives an extension update, so a shape written by an older
  // build can still be sitting there.  A half-read is worse than a miss:
  // it would draw with `features` undefined.
  it('treats a malformed record as nothing held', async () => {
    await chrome.storage.local.set({ poi_held_shower: { version: 'v1' } });
    expect(await readHeld('shower')).toBeNull();
    await chrome.storage.local.set({ poi_held_shower: 'not an object' });
    expect(await readHeld('shower')).toBeNull();
  });
});
