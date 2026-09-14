/**
 * The whole-layer hold.
 *
 * Run against `fake-indexeddb`, which is a real IndexedDB
 * implementation rather than a stub of the calls this module happens to
 * make — so a transaction that aborts, a key that is missing, and an
 * upgrade that has not run behave here the way they behave in a
 * browser.  A hand-rolled mock would agree with my idea of the API,
 * which is the thing under test.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';

import { planFor, readHeld, writeHeld, dropHeld, _resetDbForTests } from './held';
import type { PoiFeature } from './layers';

const pt = (lat: number, lng: number, name: string): PoiFeature => ({
  type: 'Feature',
  geometry: { type: 'Point', coordinates: [lng, lat] },
  properties: { name },
});

beforeEach(() => {
  // A brand-new storage backend per test — the shim's own reset, so no
  // test can read what another one wrote.
  globalThis.indexedDB = new IDBFactory();
  _resetDbForTests();
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

  // The one that matters: a layer the server cannot hand over whole
  // must keep asking per viewport.  Drawing an empty layer instead
  // would be a failure wearing the shape of an answer — the map would
  // say "there is no fuel here", which is a different claim from "I
  // could not ask".
  it('falls back to the viewport path when the server has no version', () => {
    expect(planFor(null, null)).toBe('per-view');
    expect(planFor('2026-09-14T03:12:00Z', null)).toBe('per-view');
    expect(planFor(null, undefined)).toBe('per-view');
  });
});

describe('the store', () => {
  it('gives back what it was given', async () => {
    const held = {
      version: '2026-09-14T03:12:00Z',
      features: [pt(41.8, -87.6, 'Pilot')],
      sourceAsOf: '2026-06-01T00:00:00Z',
    };
    expect(await writeHeld('fuel_station', held)).toBe(true);
    expect(await readHeld('fuel_station')).toEqual(held);
  });

  it('keeps the two dates apart', async () => {
    await writeHeld('fuel_station', {
      version: '2026-09-14T03:12:00Z',
      features: [],
      sourceAsOf: '2026-06-01T00:00:00Z',
    });
    const got = await readHeld('fuel_station');
    // The version is what a client compares; sourceAsOf is what it
    // shows.  A build that collapsed them would read the import time
    // as the data's age and call a June extract fresh.
    expect(got?.version).toBe('2026-09-14T03:12:00Z');
    expect(got?.sourceAsOf).toBe('2026-06-01T00:00:00Z');
  });

  it('keeps layers apart', async () => {
    await writeHeld('fuel_station', { version: 'v1', features: [pt(41.8, -87.6, 'Pilot')] });
    await writeHeld('shower', { version: 'v2', features: [] });
    expect((await readHeld('fuel_station'))?.version).toBe('v1');
    expect((await readHeld('shower'))?.version).toBe('v2');
  });

  it('replaces a layer wholesale rather than merging into it', async () => {
    // The import sweeps with a hard DELETE, so a point that left OSM has
    // to leave the held copy too.  Merging would keep a truck stop that
    // shut — which is the reason this is version-and-replace and not a
    // delta.
    await writeHeld('shower', {
      version: 'v1', features: [pt(41.8, -87.6, 'Gone'), pt(41.9, -87.7, 'Still there')],
    });
    await writeHeld('shower', { version: 'v2', features: [pt(41.9, -87.7, 'Still there')] });
    const got = await readHeld('shower');
    expect(got?.features.map((f) => f.properties?.name)).toEqual(['Still there']);
  });

  it('answers null for a layer it never held', async () => {
    expect(await readHeld('rest_area')).toBeNull();
  });

  it('forgets on demand', async () => {
    await writeHeld('shower', { version: 'v1', features: [] });
    await dropHeld('shower');
    expect(await readHeld('shower')).toBeNull();
  });

  it('treats a malformed record as nothing held', async () => {
    await writeHeld('shower', { version: 'v1', features: [] });
    // Reach past the module and write a shape an older build might have
    // left: the guard has to hold against the store, not against the
    // writer beside it.
    const db = await new Promise<IDBDatabase>((resolve) => {
      const r = indexedDB.open('4truck-poi', 1);
      r.onsuccess = () => resolve(r.result);
    });
    await new Promise<void>((resolve) => {
      const t = db.transaction('layers', 'readwrite');
      t.oncomplete = () => resolve();
      t.objectStore('layers').put({ version: 'v1' }, 'shower');
    });
    expect(await readHeld('shower')).toBeNull();
  });

  // Storage is a convenience, never a dependency: a private window, a
  // blocked origin or a quota refusal must cost the download and
  // nothing else.
  it('survives a browser with no IndexedDB at all', async () => {
    (globalThis as { indexedDB?: IDBFactory }).indexedDB = undefined;
    _resetDbForTests();
    expect(await readHeld('shower')).toBeNull();
    expect(await writeHeld('shower', { version: 'v1', features: [] })).toBe(false);
    await expect(dropHeld('shower')).resolves.toBeUndefined();
  });
});
