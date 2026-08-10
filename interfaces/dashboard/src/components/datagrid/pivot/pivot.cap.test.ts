/**
 * The generated-column cap.
 *
 * Leaf count is the number of distinct COMBINATIONS of the column
 * dimensions present in the data, so it multiplies: a handful of
 * dimensions over a few thousand rows generates hundreds to low
 * thousands of columns. Nothing bounded it, and an unbounded matrix
 * fails twice — it renders slowly, and it was never readable.
 *
 * What makes truncation defensible rather than a wrong answer is the
 * accumulation loop: row totals and the grand total are pushed from
 * EVERY source row, independently of which buckets render. So capping
 * costs you columns and costs the figures nothing. That property is the
 * most important thing here, and the one most likely to be broken by a
 * future refactor that "optimises" totals to sum the visible cells.
 */
import { describe, it, expect } from 'vitest';
import { pivot, MAX_COLUMN_BUCKETS, type PivotModel } from './pivot';
import type { AnyColumn } from '../../../types';

const COLUMNS: AnyColumn[] = [
  { key: 'customer', label: 'Customer', pivotable: true },
  { key: 'driver', label: 'Driver', pivotable: true },
  { key: 'rate', label: 'Rate', aggregable: true },
];

const MODEL: PivotModel = {
  rows: ['customer'],
  columns: ['driver'],
  values: [{ key: 'rate', aggFn: 'sum' }],
};

/** ``n`` distinct drivers, one row each, every rate = 1. */
const rowsWithDrivers = (n: number) => Array.from({ length: n }, (_, i) => ({
  customer: 'Acme',
  driver: `Driver ${String(i).padStart(4, '0')}`,
  rate: 1,
}));

describe('column cap', () => {
  it('does nothing to a report that fits', () => {
    const r = pivot(rowsWithDrivers(10), MODEL, COLUMNS);
    expect(r.cappedColumns).toBe(0);
    expect(r.leafIds).toHaveLength(10);
  });

  it('is inclusive at exactly the limit — off-by-one would truncate a legal report', () => {
    const r = pivot(rowsWithDrivers(MAX_COLUMN_BUCKETS), MODEL, COLUMNS);
    expect(r.cappedColumns).toBe(0);
    expect(r.leafIds).toHaveLength(MAX_COLUMN_BUCKETS);
  });

  it('caps the rendered columns and REPORTS how many it withheld', () => {
    const over = MAX_COLUMN_BUCKETS + 37;
    const r = pivot(rowsWithDrivers(over), MODEL, COLUMNS);
    expect(r.leafIds).toHaveLength(MAX_COLUMN_BUCKETS);
    expect(r.headerLevels[0]).toHaveLength(MAX_COLUMN_BUCKETS);
    // Never silent: the surface can only say "200 of 237" if the
    // transform hands it the 37.
    expect(r.cappedColumns).toBe(37);
  });

  // ── the property that makes the cap honest ─────────────────────────
  it('leaves the row total and grand total covering EVERY row', () => {
    const over = MAX_COLUMN_BUCKETS + 50;      // 250 rows, rate 1 each
    const r = pivot(rowsWithDrivers(over), MODEL, COLUMNS);
    const acme = r.bodyRows.find((b) => b.label === 'Acme')!;

    // 250, not 200 — the total describes the DATA, not the columns that
    // happened to fit on screen.
    expect(acme.totals[0]).toBe(over);
    expect(r.grandRowTotal[0]).toBe(over);
    // And the row's own population is unchanged by the cap.
    expect(acme.count).toBe(over);

    // Summing the RENDERED cells gives the smaller, truncated figure —
    // stated here so the difference is deliberate and visible: this is
    // exactly what a "sum the visible cells" refactor would silently
    // turn the total into.
    const visible = acme.cells.reduce<number>((a, c) => a + (c ?? 0), 0);
    expect(visible).toBe(MAX_COLUMN_BUCKETS);
    expect(visible).not.toBe(acme.totals[0]);
  });

  it('counts what would really have been drawn — after empty columns go', () => {
    // 20 drivers carry a measure; 300 more exist with no finite value.
    // With hideEmptyColumns on, the 300 are removed BEFORE the cap, so
    // nothing is capped: capping first would have reported a limit the
    // reader never actually hit.
    const rows = [
      ...Array.from({ length: 20 }, (_, i) => ({
        customer: 'Acme', driver: `A${String(i).padStart(3, '0')}`, rate: 5,
      })),
      ...Array.from({ length: 300 }, (_, i) => ({
        customer: 'Acme', driver: `B${String(i).padStart(3, '0')}`, rate: null,
      })),
    ];
    const r = pivot(rows, { ...MODEL, hideEmptyColumns: true }, COLUMNS);
    expect(r.cappedColumns).toBe(0);
    expect(r.leafIds).toHaveLength(20);
  });
});
