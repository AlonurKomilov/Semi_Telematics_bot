import { describe, it, expect } from 'vitest';

import {
  TILE_TYPE_FOR, copyrightText, shouldAbandonGoogle, viewportParams,
} from './googleTiles';

describe('the picker speaks Google', () => {
  it('maps every map type to a tile type', () => {
    expect(TILE_TYPE_FOR.standard).toBe('roadmap');
    expect(TILE_TYPE_FOR.satellite).toBe('satellite');
    expect(TILE_TYPE_FOR.terrain).toBe('terrain');
  });
});

describe('the viewport request', () => {
  it('rounds the zoom Leaflet allows to be fractional and clamps latitudes', () => {
    const p = viewportParams({ north: 89.9, south: -89.9, east: -66, west: -125 }, 5.4);
    expect(p).toEqual({ zoom: '5', north: '85', south: '-85', east: '-66', west: '-125' });
  });
  it('never asks for a zoom Google does not have', () => {
    expect(viewportParams({ north: 1, south: 0, east: 1, west: 0 }, 30).zoom).toBe('22');
    expect(viewportParams({ north: 1, south: 0, east: 1, west: 0 }, -2).zoom).toBe('0');
  });
});

describe('the copyright line', () => {
  it("is Google's own words when they have arrived", () => {
    expect(copyrightText('Map data ©2026 Google, INEGI')).toBe('Map data ©2026 Google, INEGI');
  });
  it('is never empty between pans', () => {
    expect(copyrightText('')).toMatch(/Google/);
    expect(copyrightText(undefined)).toMatch(/Map data ©\d{4} Google/);
  });
});

describe('giving up on Google', () => {
  it('needs several failures that also outnumber successes — a spent quota, not one slow tile', () => {
    expect(shouldAbandonGoogle(4, 0)).toBe(true);
    expect(shouldAbandonGoogle(4, 4)).toBe(true);
    expect(shouldAbandonGoogle(3, 0)).toBe(false);
    expect(shouldAbandonGoogle(6, 20)).toBe(false);
  });
});
