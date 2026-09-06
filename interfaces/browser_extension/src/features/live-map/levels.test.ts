import { describe, it, expect } from 'vitest';
import { clampPct, levelsOf, LOW_LEVEL_PCT } from './levels';

describe('fuel and DEF levels', () => {
  it('draws only what the truck reports — a missing sensor is not a zero', () => {
    expect(levelsOf({ fuel_percent: 68 }).map((l) => l.key)).toEqual(['fuel']);
    expect(levelsOf({ def_percent: 34 }).map((l) => l.key)).toEqual(['def']);
    expect(levelsOf({})).toEqual([]);
    expect(levelsOf({ fuel_percent: null, def_percent: null })).toEqual([]);
  });
  it('a reported zero IS drawn — an empty tank is the thing worth seeing', () => {
    const [fuel] = levelsOf({ fuel_percent: 0 });
    expect(fuel).toEqual({ key: 'fuel', label: 'Fuel', pct: 0, low: true });
  });
  it('flags low at the same threshold as the icon ring', () => {
    expect(levelsOf({ def_percent: LOW_LEVEL_PCT - 1 })[0].low).toBe(true);
    expect(levelsOf({ def_percent: LOW_LEVEL_PCT })[0].low).toBe(false);
  });
  it('a bar never leaves its track, whatever the provider sends', () => {
    expect(clampPct(120)).toBe(100);
    expect(clampPct(-5)).toBe(0);
    expect(clampPct(67.6)).toBe(68);
    expect(clampPct(NaN)).toBe(0);
  });
  it('both, in reading order', () => {
    expect(levelsOf({ fuel_percent: 50, def_percent: 20 }).map((l) => l.label)).toEqual(['Fuel', 'DEF']);
  });
});
