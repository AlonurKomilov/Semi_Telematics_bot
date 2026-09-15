/**
 * Bubbles, so the whole country fits on a 320px column.
 *
 * The panel holds a layer whole now, so a country view filters every
 * point into the draw — 2,340 weigh stations — and the old nearest-N cap
 * turned that into "Nearest 250 shown — zoom in for the rest", on a map
 * the dashboard shows entire.
 *
 * THE MEASUREMENT THAT SAID THE CAP WAS SAFE WENT STALE UNDER IT.  It
 * was taken when a pan fetched one viewport and the server refused a box
 * bigger than a few degrees, so a wide zoom drew nothing and 250 was
 * never reached.  Holding the layer changed the mechanism the number was
 * measured against, and nobody re-measured — this file is the
 * re-measurement, expressed as rules rather than as a number.
 */
import { describe, it, expect } from 'vitest';
import { clusterByGrid, degreesPerPixel, nearestClustersFirst } from './viewport';
import type { PoiFeature } from './viewport';

const pt = (lat: number, lng: number, name = ''): PoiFeature => ({
  type: 'Feature',
  geometry: { type: 'Point', coordinates: [lng, lat] },
  properties: { name },
});

describe('degreesPerPixel', () => {
  it('is the Web Mercator relation, not a guess', () => {
    // 360 degrees across 256 pixels at zoom 0.
    expect(degreesPerPixel(0, 256)).toBeCloseTo(360, 6);
    // Each zoom level halves it.
    expect(degreesPerPixel(1, 256)).toBeCloseTo(180, 6);
    expect(degreesPerPixel(4, 44)).toBeCloseTo((44 * 360) / (256 * 16), 9);
  });
});

describe('clusterByGrid', () => {
  it('keeps every point — a bubble hides nothing', () => {
    const pts = Array.from({ length: 300 }, (_, i) =>
      pt(30 + (i % 20) * 0.7, -100 + Math.floor(i / 20) * 0.7));
    const total = clusterByGrid(pts, 2).reduce((n, c) => n + c.count, 0);
    expect(total).toBe(300);
  });

  it('turns a country of points into a screenful of markers', () => {
    // 2,340 across the lower 48, which is what the DOT layer actually
    // holds — the number the old cap sliced to 250.
    const pts = Array.from({ length: 2340 }, (_, i) =>
      pt(25 + (i % 45) * 0.55, -124 + Math.floor(i / 45) * 1.1));
    const cells = clusterByGrid(pts, degreesPerPixel(4, 44));
    expect(cells.length).toBeLessThan(250);
    expect(cells.reduce((n, c) => n + c.count, 0)).toBe(2340);
  });

  it('gives a lone point back as itself, so it keeps its popup', () => {
    const only = pt(41.8, -87.6, 'Pilot');
    const [c] = clusterByGrid([only], 1);
    expect(c.count).toBe(1);
    expect(c.one).toBe(only);
  });

  it('a bubble of several carries no single feature', () => {
    const [c] = clusterByGrid([pt(41.80, -87.60), pt(41.81, -87.61)], 1);
    expect(c.count).toBe(2);
    expect(c.one).toBeNull();
  });

  it('sits where its points are, not on the grid', () => {
    // The mean, not the cell corner: a bubble pinned to a grid reads as
    // a grid, and the eye notices before the mind does.
    // Both inside one cell — chosen by arithmetic, not by eye: with a
    // 10-degree cell, lng/10 floors to -10 for both, and lat/(10·cos40)
    // floors to 5 for both.  A negative floor is where this is easy to
    // get wrong, and the first version of this test did.
    const cs = clusterByGrid([pt(40, -100), pt(42, -96)], 10);
    expect(cs).toHaveLength(1);
    expect(cs[0].count).toBe(2);
    expect(cs[0].lat).toBeCloseTo(41, 6);
    expect(cs[0].lng).toBeCloseTo(-98, 6);
  });

  it('measures the cell in SCREEN pixels, not in degrees of latitude', () => {
    // Mercator stretches north-south as you leave the equator: at 60°N a
    // degree of latitude covers twice the pixels it does at the equator,
    // so a cell that is 44px tall must span HALF as many degrees there.
    //
    // The discriminating case, and the first version of this test did
    // not have one — it asserted that two latitudes behaved the SAME,
    // which was true with the correction and true without it.  It passed
    // over the mutation that deleted the thing it was written to protect.
    //
    // 0.7° apart at 60°N: under one cell if cells are measured in
    // degrees (the bug), over one cell if measured in pixels (correct).
    const cells = clusterByGrid([pt(60.0, -100), pt(60.7, -100)], 1);
    expect(cells, 'these are more than a screen cell apart at 60°N and '
      + 'must not merge — the cell height is being read in degrees')
      .toHaveLength(2);

    // …and the same pair near the equator, where a degree is a cell, is
    // one bubble.  Same degrees, different answer, which is the point.
    expect(clusterByGrid([pt(0.0, -100), pt(0.7, -100)], 1)).toHaveLength(1);
  });

  it('draws every point when the cell is nothing', () => {
    const pts = [pt(1, 1), pt(1, 1), pt(2, 2)];
    expect(clusterByGrid(pts, 0)).toHaveLength(3);
  });
});

describe('nearestClustersFirst', () => {
  // Its own function rather than a cast of the feature version: a
  // cluster carries lat/lng and a feature carries geometry.coordinates,
  // so `as unknown as` type-checks and then reads undefined the first
  // time the cap actually bites — at country zoom, in front of somebody.
  it('returns the input untouched when it fits', () => {
    const cs = clusterByGrid([pt(1, 1), pt(9, 9)], 1);
    expect(nearestClustersFirst(cs, 0, 0, 10)).toBe(cs);
  });

  it('keeps the ones nearest the centre when it does not', () => {
    const cs = clusterByGrid([pt(0.1, 0.1), pt(50, 50), pt(60, 60)], 0.01);
    const kept = nearestClustersFirst(cs, 0, 0, 1);
    expect(kept).toHaveLength(1);
    expect(kept[0].lat).toBeCloseTo(0.1, 6);
  });

  it('reads lat/lng and never geometry', () => {
    // The cast this replaced would throw here.
    const cs = [{ lat: 5, lng: 5, count: 3, one: null }];
    expect(() => nearestClustersFirst(cs, 0, 0, 0)).not.toThrow();
  });
});
