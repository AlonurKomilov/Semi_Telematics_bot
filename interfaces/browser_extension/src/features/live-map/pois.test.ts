/**
 * The Map Layers' arithmetic, held to what it promises.
 *
 * Three of these exist because the alternative is a bug nobody sees:
 * a bbox that drifts from the dashboard's re-asks Overpass for squares
 * that are already answered; a cap that keeps the WRONG markers shows
 * a driver the far side of the state; and a brand chip that matches on
 * substring turns "station" into a TA.
 */
import { describe, expect, it, beforeEach } from 'vitest';

import {
  MARKER_BUDGET, bboxCovers, bboxKey, bboxParam, brandMatch, expandedBbox,
  filterToView, lsFindCovering, lsRead, lsWrite, nearestFirst, parseBboxKey,
  snapToGrid, wordMatch, LS_STALE_MS, type PoiFeature,
} from './pois';

const at = (lat: number, lng: number, props: Record<string, unknown> = {}): PoiFeature => ({
  type: 'Feature', geometry: { type: 'Point', coordinates: [lng, lat] }, properties: props,
});

const view = (south: number, west: number, north: number, east: number) =>
  ({ south, west, north, east });

describe('the grid is the dashboard’s grid', () => {
  it('snaps down, including below zero', () => {
    expect(snapToGrid(41.7)).toBe(41);
    // Math.floor, not truncation: -41.2 belongs to the cell starting -42.
    expect(snapToGrid(-41.2)).toBe(-42);
  });

  it('grows the snapped box by a whole cell on every side', () => {
    expect(expandedBbox(view(41.7, -87.9, 42.1, -87.2))).toEqual([40, -89, 43, -87]);
  });

  it('the view it was built from is strictly inside it', () => {
    const v = view(41.7, -87.9, 42.1, -87.2);
    expect(bboxCovers(bboxKey(v), v)).toBe(true);
  });

  it('clamps at the poles and the date line instead of asking for a box that cannot exist', () => {
    const [s, w, n, e] = expandedBbox(view(-89.5, 179.2, 89.5, 179.9));
    expect(s).toBeGreaterThanOrEqual(-90);
    expect(n).toBeLessThanOrEqual(90);
    expect(w).toBeGreaterThanOrEqual(-180);
    expect(e).toBeLessThanOrEqual(180);
    expect(n).toBeGreaterThan(s);
    expect(e).toBeGreaterThan(w);
  });

  it('spells the key and the request from ONE box', () => {
    const v = view(41.7, -87.9, 42.1, -87.2);
    expect(parseBboxKey(bboxKey(v))).toEqual(bboxParam(v).split(',').map(Number));
  });

  it('refuses a key it cannot read rather than guessing at one', () => {
    expect(parseBboxKey('40,-89,44')).toBeNull();
    expect(parseBboxKey('a,b,c,d')).toBeNull();
    expect(bboxCovers('nonsense', view(0, 0, 1, 1))).toBe(false);
  });
});

describe('zooming in costs nothing', () => {
  it('a wider cached box covers a narrower view', () => {
    expect(bboxCovers('40,-89,44,-87', view(41, -88, 42, -87.5))).toBe(true);
  });

  it('a box that misses one edge does NOT cover it', () => {
    expect(bboxCovers('40,-89,44,-87', view(41, -88, 42, -86))).toBe(false);
  });

  it('filtering keeps what is inside and drops what is not', () => {
    const kept = filterToView([at(41.5, -88), at(48, -88), at(41.5, -70)], view(41, -89, 42, -87));
    expect(kept).toHaveLength(1);
    expect(kept[0].geometry.coordinates).toEqual([-88, 41.5]);
  });
});

describe('the marker cap keeps the nearest, and only when it has to', () => {
  it('returns the same array untouched when everything fits', () => {
    const few = [at(41, -88), at(41.1, -88)];
    expect(nearestFirst(few, 41, -88)).toBe(few);
  });

  it('keeps the close ones and drops the far ones', () => {
    const near = Array.from({ length: 5 }, (_, i) => at(41 + i * 0.01, -88));
    const far  = Array.from({ length: 5 }, (_, i) => at(48 + i * 0.01, -88));
    const kept = nearestFirst([...far, ...near], 41, -88, 5);
    expect(kept).toHaveLength(5);
    expect(kept.every((f) => f.geometry.coordinates[1] < 42)).toBe(true);
  });

  it('weights longitude by latitude, so north-south and east-west rank alike', () => {
    // At 60°N a degree of longitude is half a degree of latitude on the
    // ground.  Without the cosine the eastern point would look twice as
    // far as it is and be the one thrown away.
    const north = at(61, 0);       // 1° of latitude away
    const east  = at(60, 1.5);     // ~0.75° on the ground
    const kept = nearestFirst([north, east], 60, 0, 1);
    expect(kept[0]).toBe(east);
  });

  it('has a budget an honest count can be shown against', () => {
    expect(MARKER_BUDGET).toBeGreaterThan(0);
  });
});

describe('a brand chip matches words, not letters', () => {
  it('finds TA where TA is a word', () => {
    expect(wordMatch('TA Travel Center', 'TA')).toBe(true);
    expect(wordMatch('TA-Petro #145', 'TA')).toBe(true);
  });

  it('does not find TA inside "station", nor BP inside "Sapp Bros"', () => {
    expect(wordMatch('Gas station', 'TA')).toBe(false);
    expect(wordMatch('Sapp Bros', 'BP')).toBe(false);
  });

  it('reads all three fields a chain can hide in', () => {
    expect(brandMatch(['Pilot'], at(0, 0, { operator: 'Pilot Travel Centers LLC' }))).toBe(true);
    expect(brandMatch(['Pilot'], at(0, 0, { brand: 'Pilot' }))).toBe(true);
    expect(brandMatch(['Pilot'], at(0, 0, { name: "Love's #12" }))).toBe(false);
  });

  it('survives a name with regex in it instead of throwing', () => {
    expect(() => wordMatch('Bob (+) Fuel', 'Bob (+)')).not.toThrow();
  });

  it('reads a feature whose properties are null', () => {
    const bare: PoiFeature = { type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: null };
    expect(brandMatch(['Pilot'], bare)).toBe(false);
  });
});

describe('the cache survives the panel being closed', () => {
  beforeEach(() => localStorage.clear());

  it('reads back what it wrote', () => {
    lsWrite('fuel_station', '40,-89,44,-87', [at(41, -88)]);
    expect(lsRead('fuel_station', '40,-89,44,-87')?.features).toHaveLength(1);
  });

  it('refuses an entry past its hard expiry, and forgets it', () => {
    lsWrite('fuel_station', '40,-89,44,-87', [at(41, -88)]);
    const later = Date.now() + LS_STALE_MS + 1;
    expect(lsRead('fuel_station', '40,-89,44,-87', later)).toBeNull();
    // Read again at the ORIGINAL time: a rejected entry is deleted, not
    // merely hidden, so the quota it held comes back.
    expect(lsRead('fuel_station', '40,-89,44,-87')).toBeNull();
  });

  it('finds a wider cached box for a view it has never seen', () => {
    lsWrite('fuel_station', '40,-89,44,-87', [at(41.5, -88), at(43.9, -87.1)]);
    const found = lsFindCovering('fuel_station', view(41, -88.5, 42, -87.5));
    expect(found?.features).toHaveLength(2);
  });

  it('does not hand one layer’s cache to another', () => {
    lsWrite('fuel_station', '40,-89,44,-87', [at(41.5, -88)]);
    expect(lsFindCovering('truck_parking', view(41, -88.5, 42, -87.5))).toBeNull();
  });
});
