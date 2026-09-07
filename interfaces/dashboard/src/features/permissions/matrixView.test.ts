import { describe, it, expect } from 'vitest';
import { buildVerbGrid } from './verbGrid';
import { bandAnchor, bandRows, bandSummary, familyMatches, viewRows } from './matrixView';
import { BAND_MODULE, GROUP_MODULE } from './permRows';

const GRID = buildVerbGrid();
const fleet = GRID.bands.find((b) => b.band === 'Fleet')!;
const shared = GRID.bands.find((b) => b.band === 'Shared')!;

describe('the band summary counts every tickable row', () => {
  it('parents, their manage rows and children, once each', () => {
    const rows = bandRows(fleet.families);
    const keys = rows.map((r) => ('allKey' in r ? r.allKey : (r as { key: string }).key));
    expect(new Set(keys).size).toBe(keys.length);
    expect(rows.length).toBeGreaterThan(fleet.families.length);   // Manage rows and children add up
  });

  it('reports n of m against the caller’s answer', () => {
    const all = bandSummary(fleet.families, () => true);
    expect(all.granted).toBe(all.total);
    const none = bandSummary(fleet.families, () => false);
    expect(none).toEqual({ granted: 0, total: all.total });
  });
});

describe('grant-all reaches the view rows only', () => {
  it('never a Manage row', () => {
    const manage = new Set(fleet.families.flatMap((f) => (f.manage ? [f.manage] : [])));
    const manageChildren = new Set(fleet.families.flatMap((f) => f.children.filter((c) => c.verb === 'manage').map((c) => c.row)));
    for (const r of viewRows(fleet.families)) {
      expect(manage.has(r)).toBe(false);
      expect(manageChildren.has(r)).toBe(false);
    }
    expect(viewRows(fleet.families).length).toBeGreaterThanOrEqual(fleet.families.length);
  });
});

describe('the search', () => {
  it('matches a parent by label or description, and a child by either', () => {
    const vehicles = shared.families.find((f) => f.parent.label === 'Vehicles')!;
    expect(familyMatches(vehicles, 'VEHIC')).toBe(true);
    expect(familyMatches(vehicles, 'fuel')).toBe(true);          // a child's label
    expect(familyMatches(vehicles, 'no such feature')).toBe(false);
  });

  it('an empty query matches everything', () => {
    for (const b of GRID.bands) for (const f of b.families) expect(familyMatches(f, '   ')).toBe(true);
  });
});

describe('the anchor', () => {
  it('is a stable, id-safe slug of the band title', () => {
    expect(bandAnchor('Fleet')).toBe('perm-band-fleet');
    expect(bandAnchor('Owner powers')).toBe('perm-band-owner-powers');
    expect(bandAnchor('HR')).toBe('perm-band-hr');
  });
});

describe('the department echo is keyed by band titles that exist', () => {
  // Two hand-typed literals with no compile-time link: a renamed band
  // would silently lose its "department off" echo.  Pin them together.
  it('every GROUP_MODULE and BAND_MODULE key is a band of the grid', () => {
    const titles = new Set(GRID.bands.map((b) => b.band));
    for (const key of [...Object.keys(GROUP_MODULE), ...Object.keys(BAND_MODULE)]) {
      expect(titles.has(key), `${key} is not a band title`).toBe(true);
    }
  });

  it('Recruiting rides the HR switch — the catalog puts its features in hr', () => {
    expect(BAND_MODULE.Recruiting).toBe('hr');
    expect(GROUP_MODULE.Recruiting).toBeUndefined();     // no second HR chip in the top bar
  });
});
