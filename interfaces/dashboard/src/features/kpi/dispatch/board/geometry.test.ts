/**
 * The bar rules, argued with directly.  Every case here is a defect
 * that reached a screenshot before the geometry was extractable: the
 * clamp printed as a delivery date, the strip drawn on lane 0 for a
 * lane-1 load, a label's runway computed from arithmetic while the
 * board rendered something shorter.
 */
import { describe, it, expect } from 'vitest';
import { boardGeometry, dayRange, deliveryEnd, prevDay } from './geometry';
import type { RunLoad } from '../../api';

const load = (p: Partial<RunLoad> & { pickup_date: string }): RunLoad => ({
  load_number: 'L1', status: 'delivered',
  delivery_date: '', pickup_location: 'A', delivery_location: 'B',
  total_rate: 1000, miles: 500,
  ...p,
});

const WEEK = { windowStart: '2026-08-10', windowEnd: '2026-08-16' };
const NONE = new Map<string, string>();
const NO_SUG = new Map<string, unknown>();

describe('dayRange', () => {
  it('is inclusive of both ends', () => {
    expect(dayRange('2026-08-10', '2026-08-16')).toEqual([
      '2026-08-10', '2026-08-11', '2026-08-12', '2026-08-13',
      '2026-08-14', '2026-08-15', '2026-08-16',
    ]);
  });

  it('crosses a month boundary without a timezone slipping the day', () => {
    expect(dayRange('2026-07-31', '2026-08-02'))
      .toEqual(['2026-07-31', '2026-08-01', '2026-08-02']);
  });

  it('is one day when start === end, and empty when reversed', () => {
    expect(dayRange('2026-08-10', '2026-08-10')).toEqual(['2026-08-10']);
    expect(dayRange('2026-08-16', '2026-08-10')).toEqual([]);
  });
});

describe('deliveryEnd — the REAL date, which words use', () => {
  it('is the delivery date when it is after the pickup', () => {
    expect(deliveryEnd(load({ pickup_date: '2026-08-14', delivery_date: '2026-08-18' })))
      .toBe('2026-08-18');
  });

  it('falls back to the pickup day when delivery is missing or not later', () => {
    expect(deliveryEnd(load({ pickup_date: '2026-08-14' }))).toBe('2026-08-14');
    expect(deliveryEnd(load({ pickup_date: '2026-08-14', delivery_date: '2026-08-13' })))
      .toBe('2026-08-14');
  });

  it('survives a timestamp — dates are compared as calendar strings', () => {
    expect(deliveryEnd(load({
      pickup_date: '2026-08-14', delivery_date: '2026-08-18T23:30:00Z',
    }))).toBe('2026-08-18');
  });
});

describe('spanEnd vs deliveryEnd — the clamp never becomes a date', () => {
  it('clamps the PIXELS at the window end while the WORDS keep the real day', () => {
    const l = load({ pickup_date: '2026-08-14', delivery_date: '2026-08-18' });
    const g = boardGeometry({ loads: [l], ...WEEK, marks: NONE, suggested: NO_SUG });
    expect(g.spanEnd(l)).toBe('2026-08-16');     // geometry stops at Sunday
    expect(g.deliveryEnd(l)).toBe('2026-08-18'); // the label still says Tuesday
  });

  it('counts "day N of TOTAL" over the REAL trip, not the visible slice', () => {
    const l = load({ pickup_date: '2026-08-14', delivery_date: '2026-08-18' });
    const g = boardGeometry({ loads: [l], ...WEEK, marks: NONE, suggested: NO_SUG });
    // Fri→Tue is a 5-day trip; only Sat and Sun are visible.
    expect(g.transitInfo.get('2026-08-15')).toMatchObject({ dayNo: 2, total: 5 });
    expect(g.transitInfo.get('2026-08-16')).toMatchObject({ dayNo: 3, total: 5 });
    expect(g.transitInfo.has('2026-08-17')).toBe(false);
  });
});

describe('stripLoadAt — what a day actually RENDERS', () => {
  const rolling = load({
    load_number: 'ROLL', pickup_date: '2026-08-10', delivery_date: '2026-08-14',
  });

  it('claims the days between pickup and delivery, but never the pickup day', () => {
    const g = boardGeometry({ loads: [rolling], ...WEEK, marks: NONE, suggested: NO_SUG });
    expect(g.stripLoadAt('2026-08-10')).toBeNull();
    expect(g.stripLoadAt('2026-08-11')).toBe(rolling);
    expect(g.stripLoadAt('2026-08-14')).toBe(rolling);
    expect(g.stripLoadAt('2026-08-15')).toBeNull();
  });

  it('yields to a day that has its own pickup — the bar ENDS at a foreign chip', () => {
    const other = load({ load_number: 'OWN', pickup_date: '2026-08-12' });
    const g = boardGeometry({
      loads: [rolling, other], ...WEEK, marks: NONE, suggested: NO_SUG,
    });
    expect(g.stripLoadAt('2026-08-11')).toBe(rolling);
    expect(g.stripLoadAt('2026-08-12')).toBeNull();  // OWN's chip renders here
    expect(g.stripLoadAt('2026-08-13')).toBe(rolling);
  });

  it('yields to an inactive mark and to a repair suggestion', () => {
    const marked = boardGeometry({
      loads: [rolling], ...WEEK,
      marks: new Map([['2026-08-12', 'repair']]), suggested: NO_SUG,
    });
    expect(marked.stripLoadAt('2026-08-12')).toBeNull();

    const suggested = boardGeometry({
      loads: [rolling], ...WEEK, marks: NONE,
      suggested: new Map([['2026-08-13', { reason: 'repair' }]]),
    });
    expect(suggested.stripLoadAt('2026-08-13')).toBeNull();
  });

  it('renders nothing outside the row window', () => {
    const early = load({ pickup_date: '2026-08-08', delivery_date: '2026-08-12' });
    const g = boardGeometry({ loads: [early], ...WEEK, marks: NONE, suggested: NO_SUG });
    expect(g.stripLoadAt('2026-08-09')).toBeNull();  // before the window
    expect(g.stripLoadAt('2026-08-11')).toBe(early);
  });
});

describe('stripRun — the label runway follows what RENDERS, not arithmetic', () => {
  it('counts the strip days a long load actually gets', () => {
    const l = load({ pickup_date: '2026-08-10', delivery_date: '2026-08-13' });
    const g = boardGeometry({ loads: [l], ...WEEK, marks: NONE, suggested: NO_SUG });
    expect(g.stripRun(l, '2026-08-10')).toBe(3);
  });

  it('stops at the interruption — the text may not run under a foreign chip', () => {
    const l = load({ load_number: 'LONG', pickup_date: '2026-08-10', delivery_date: '2026-08-15' });
    const blocker = load({ load_number: 'BLOCK', pickup_date: '2026-08-12' });
    const g = boardGeometry({
      loads: [l, blocker], ...WEEK, marks: NONE, suggested: NO_SUG,
    });
    // Five calendar days of span, but only ONE renders before the
    // Wednesday chip — the collision that put "$2,150" over a city.
    expect(g.stripRun(l, '2026-08-10')).toBe(1);
  });

  it('is zero for a single-day load', () => {
    const l = load({ pickup_date: '2026-08-11' });
    const g = boardGeometry({ loads: [l], ...WEEK, marks: NONE, suggested: NO_SUG });
    expect(g.stripRun(l, '2026-08-11')).toBe(0);
  });
});

describe("laneOf — a strip draws on ITS load's line", () => {
  it("gives each of a day's loads its own lane, in pickup order", () => {
    const first = load({ load_number: 'A', pickup_date: '2026-08-10' });
    const second = load({
      load_number: 'B', pickup_date: '2026-08-10', delivery_date: '2026-08-13',
    });
    const g = boardGeometry({
      loads: [first, second], ...WEEK, marks: NONE, suggested: NO_SUG,
    });
    expect(g.laneOf(first)).toBe(0);
    expect(g.laneOf(second)).toBe(1);
    // The rolling days belong to the LANE-1 load — drawing them on
    // lane 0 made B's bar look like it continued from A's label.
    expect(g.stripLoadAt('2026-08-11')).toBe(second);
    expect(g.laneOf(g.stripLoadAt('2026-08-11')!)).toBe(1);
  });
});

describe('first claim wins a contested rolling day', () => {
  it("keeps the earlier load's strip when two spans overlap", () => {
    const a = load({ load_number: 'A', pickup_date: '2026-08-10', delivery_date: '2026-08-13' });
    const b = load({ load_number: 'B', pickup_date: '2026-08-11', delivery_date: '2026-08-14' });
    const g = boardGeometry({ loads: [a, b], ...WEEK, marks: NONE, suggested: NO_SUG });
    // 08-11 is B's pickup, so no strip; 08-12 is contested and A holds it.
    expect(g.stripLoadAt('2026-08-11')).toBeNull();
    expect(g.transitInfo.get('2026-08-12')?.load).toBe(a);
  });
});

describe('prevDay', () => {
  it('steps back across a month boundary', () => {
    expect(prevDay('2026-08-01')).toBe('2026-07-31');
  });
});
