import { describe, it, expect } from 'vitest';

import { derivePivotDimensions, isDateColumn, DERIVED_SEP } from './derived';
import { pivot } from './pivot';
import type { AnyColumn } from '../../../types';

const TZ = 'America/Denver';

const COLS: AnyColumn[] = [
  { key: 'customer', label: 'Customer', pivotable: true },
  { key: 'delivered', label: 'Delivered', pivotable: true, aggType: 'date' },
  { key: 'created', label: 'Created', pivotable: true, filterMode: 'date-range' },
  { key: 'notes', label: 'Notes' },                       // not pivotable
  { key: 'rate', label: 'Rate', aggregable: true },
];

describe('derived pivot dimensions', () => {
  it('recognises a date column from either existing signal', () => {
    expect(isDateColumn(COLS[1])).toBe(true);   // aggType: 'date'
    expect(isDateColumn(COLS[2])).toBe(true);   // filterMode: 'date-range'
    expect(isDateColumn(COLS[0])).toBe(false);
  });

  it('replaces a date dimension with Year / Quarter / Month', () => {
    const out = derivePivotDimensions(COLS, TZ);
    const keys = out.map((c) => c.key);
    // The raw date key is GONE as a dimension — grouping by an exact
    // timestamp would make one column per row.
    expect(keys).not.toContain('delivered');
    expect(keys).toContain(`delivered${DERIVED_SEP}year`);
    expect(keys).toContain(`delivered${DERIVED_SEP}quarter`);
    expect(keys).toContain(`delivered${DERIVED_SEP}month`);
    expect(out.find((c) => c.key === `delivered${DERIVED_SEP}month`)!.label)
      .toBe('Delivered (Month)');
  });

  it('leaves non-date and non-pivotable columns untouched', () => {
    const out = derivePivotDimensions(COLS, TZ);
    expect(out.find((c) => c.key === 'customer')).toBe(COLS[0]);
    expect(out.find((c) => c.key === 'notes')).toBe(COLS[3]);
    expect(out.find((c) => c.key === 'rate')).toBe(COLS[4]);
  });

  it("respects a page's OWN pivotValue instead of overriding it", () => {
    const custom: AnyColumn = {
      key: 'delivered', label: 'Delivered', pivotable: true, aggType: 'date',
      pivotValue: () => 'CUSTOM',
    };
    const out = derivePivotDimensions([custom], TZ);
    expect(out).toHaveLength(1);
    expect(out[0]).toBe(custom);
  });

  it('never exposes a derived dimension as a real column', () => {
    const derived = derivePivotDimensions(COLS, TZ)
      .filter((c) => c.key.includes(DERIVED_SEP));
    // Sorting/filtering/aggregating a synthetic bucket column would be
    // meaningless — it isn't in the grid's column set at all.
    for (const c of derived) {
      expect(c.sortable).toBe(false);
      expect(c.filterable).toBe(false);
      expect(c.aggregable).toBe(false);
      expect(c.pivotable).toBe(true);
    }
  });
});

describe('derived dimensions bucket in the ACCOUNT timezone', () => {
  // 2026-02-01T05:30:00Z is still Jan 31 22:30 in Denver.  A UTC bucket
  // would file this load's revenue in the wrong month/quarter/year.
  const LATE = { customer: 'Acme', delivered: '2026-02-01T05:30:00Z', rate: 100 };
  const cols = (tz: string) => derivePivotDimensions(COLS, tz);

  const bucketVia = (tz: string, grain: string) => {
    const col = cols(tz).find((c) => c.key === `delivered${DERIVED_SEP}${grain}`)!;
    return col.pivotValue!(LATE);
  };

  it('buckets by month in the account tz, not UTC', () => {
    expect(bucketVia('America/Denver', 'month')).toBe('2026-01');
    expect(bucketVia('UTC', 'month')).toBe('2026-02');
  });

  it('carries the same shift through quarter and year', () => {
    expect(bucketVia('America/Denver', 'quarter')).toBe('2026-Q1');
    expect(bucketVia('UTC', 'quarter')).toBe('2026-Q1');
    // New Year's Eve is the case where the YEAR itself moves.
    const NYE = { delivered: '2027-01-01T04:00:00Z' };
    const denverYear = cols('America/Denver')
      .find((c) => c.key === `delivered${DERIVED_SEP}year`)!.pivotValue!(NYE);
    const utcYear = cols('UTC')
      .find((c) => c.key === `delivered${DERIVED_SEP}year`)!.pivotValue!(NYE);
    expect(denverYear).toBe('2026');
    expect(utcYear).toBe('2027');
  });

  it('produces quarters that sort chronologically', () => {
    const q = (iso: string) => cols(TZ)
      .find((c) => c.key === `delivered${DERIVED_SEP}quarter`)!
      .pivotValue!({ delivered: iso });
    expect([q('2026-11-02'), q('2026-02-02'), q('2026-07-02')].sort())
      .toEqual(['2026-Q1', '2026-Q3', '2026-Q4']);
  });

  it('returns an empty bucket for a missing date rather than throwing', () => {
    const col = cols(TZ).find((c) => c.key === `delivered${DERIVED_SEP}month`)!;
    expect(col.pivotValue!({})).toBe('');
    expect(col.pivotValue!({ delivered: 'not a date' })).toBe('');
  });
});

describe('derived dimensions drive a real pivot', () => {
  it('groups rate by customer x quarter with no hand-written accessor', () => {
    const cols = derivePivotDimensions(COLS, TZ);
    const rows = [
      { customer: 'Acme', delivered: '2026-01-15', rate: 100 },
      { customer: 'Acme', delivered: '2026-02-20', rate: 300 },
      { customer: 'Acme', delivered: '2026-05-05', rate: 50 },
      { customer: 'Bolt', delivered: '2026-05-09', rate: 200 },
    ];
    const r = pivot(rows, {
      rows: ['customer'],
      columns: [`delivered${DERIVED_SEP}quarter`],
      values: [{ key: 'rate', aggFn: 'sum' }],
    }, cols);

    expect(r.headerLevels[0].map((h) => h.label)).toEqual(['2026-Q1', '2026-Q2']);
    expect(r.bodyRows.find((b) => b.label === 'Acme')!.cells).toEqual([400, 50]);
    expect(r.bodyRows.find((b) => b.label === 'Bolt')!.cells).toEqual([null, 200]);
  });

  it('nests Year > Month as two header levels', () => {
    const cols = derivePivotDimensions(COLS, TZ);
    const rows = [
      { customer: 'Acme', delivered: '2025-12-10', rate: 10 },
      { customer: 'Acme', delivered: '2026-01-10', rate: 20 },
      { customer: 'Acme', delivered: '2026-02-10', rate: 30 },
    ];
    const r = pivot(rows, {
      rows: ['customer'],
      columns: [`delivered${DERIVED_SEP}year`, `delivered${DERIVED_SEP}month`],
      values: [{ key: 'rate', aggFn: 'sum' }],
    }, cols);

    expect(r.headerLevels).toHaveLength(3);
    expect(r.headerLevels[0].map((h) => h.label)).toEqual(['2025', '2026']);
    expect(r.headerLevels[0].map((h) => h.span)).toEqual([1, 2]);
    // Months drop the year HERE because the level above already states
    // it — `Dec 2025` sitting under a `2025` header says it twice.  The
    // quarter-only test above keeps `2026-Q1` fully qualified, which is
    // the same rule seen from the other side: nothing coarser is on that
    // axis, so the label is the only place the year can come from.
    expect(r.headerLevels[1].map((h) => h.label))
      .toEqual(['Dec', 'Jan', 'Feb']);
  });
});

// ── Year said once, not three times ──────────────────────────────────
//
// Stacking Quarter + Year + Month on one axis produced a header reading
// `2026-Q1 / 2026 / MAR 2026` — the same year in all three rows, so
// learning one fact meant scanning three. The coarser grain present on
// the axis is the one whose job it is to say the year; the others drop
// it. Buckets are untouched: `Q1` alone collides across years.
describe('bare labels when a coarser grain shares the axis', () => {
  const dims = derivePivotDimensions(
    [{ key: 'del', label: 'DEL date', aggType: 'date', pivotable: true }],
    'UTC',
  );
  const year = dims.find((d) => d.pivotGrain === 'year')!;
  const quarter = dims.find((d) => d.pivotGrain === 'quarter')!;
  const month = dims.find((d) => d.pivotGrain === 'month')!;

  it('strips the year the Year dimension already states', () => {
    expect(quarter.pivotLabelBare!('2026-Q1')).toBe('Q1');
    expect(month.pivotLabelBare!('2026-03')).toBe('Mar');
  });

  it('leaves the year grain alone — a year IS the year', () => {
    expect(year.pivotLabelBare!('2026')).toBe('2026');
  });

  it('keeps the fully-qualified label as the default', () => {
    // ``pivotLabel`` is what renders when nothing coarser is on the axis,
    // and it must still carry the year or the column is unreadable.
    expect(month.pivotLabel!('2026-03')).toBe('Mar 2026');
    expect(quarter.pivotLabel).toBeUndefined();   // raw '2026-Q1'
  });

  it('carries the source key, so two date columns never borrow each other\'s year', () => {
    expect(quarter.pivotSourceKey).toBe('del');
    expect(month.pivotSourceKey).toBe('del');
  });
});
