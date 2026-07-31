/**
 * Drill-down — the rows behind one cell.
 *
 * Lives beside `drill.ts` rather than in `pivot.test.ts` for the same
 * reason the source does: the drill is one feature, so its query, its
 * dialog and its tests share a home.
 *
 * The invariant these exist to hold: THE LIST MUST ACCOUNT FOR THE
 * NUMBER.  `pivotCellRows` selects rows independently of `pivot()`, and
 * the two now sit in different files — so any divergence in how they
 * filter shows up as a dialog that contradicts the figure that opened
 * it.  The last test is the one that would catch it.
 */
import { describe, it, expect } from 'vitest';

import { pivot, splitLeafId, type PivotModel } from './pivot';
import { pivotCellRows } from './drill';
import type { AnyColumn } from '../../../types';

const COLS: AnyColumn[] = [
  { key: 'company', label: 'Company', pivotable: true },
  { key: 'customer', label: 'Customer', pivotable: true },
  { key: 'region', label: 'Region', pivotable: true },
  { key: 'rate', label: 'Rate', aggregable: true },
];
const DATA = [
  { id: 1, company: 'PTG', customer: 'Acme', region: 'N', rate: 100 },
  { id: 2, company: 'PTG', customer: 'Acme', region: 'S', rate: 200 },
  { id: 3, company: 'PTG', customer: 'Bolt', region: 'N', rate: 50 },
  { id: 4, company: 'CFT', customer: 'Zed', region: 'N', rate: 10 },
];
const m: PivotModel = {
  rows: ['company', 'customer'], columns: ['region'],
  values: [{ key: 'rate', aggFn: 'sum' as const }],
};

describe('drill-down — which rows are behind this cell', () => {
  it('returns exactly the rows behind one cell', () => {
    const got = pivotCellRows(DATA, m, COLS, ['PTG', 'Acme'], ['N']);
    expect(got.map((r) => r.id)).toEqual([1]);
  });

  it('returns every descendant when the path is a COLLAPSED PARENT', () => {
    // PTG/N shows 150 (Acme 100 + Bolt 50) — the drill-down must be the
    // union, or the number and the list would disagree.
    const got = pivotCellRows(DATA, m, COLS, ['PTG'], ['N']);
    expect(got.map((r) => r.id)).toEqual([1, 3]);
  });

  it('treats an empty column path as "every column"', () => {
    const got = pivotCellRows(DATA, m, COLS, ['PTG'], []);
    expect(got.map((r) => r.id)).toEqual([1, 2, 3]);
  });

  it('returns nothing for an empty intersection', () => {
    expect(pivotCellRows(DATA, m, COLS, ['CFT'], ['S'])).toEqual([]);
  });

  it('splits a leaf id back into its column path and measure', () => {
    const r = pivot(DATA, m, COLS);
    const parsed = r.leafIds.map(splitLeafId);
    expect(parsed[0]).toEqual({ colPath: ['N'], valueKey: 'rate' });
    // No column dimension → an empty path, not [''].
    expect(splitLeafId('||rate')).toEqual({ colPath: [], valueKey: 'rate' });
  });

  it('ignores a switched-off dimension, exactly like the cells do', () => {
    // If the drill matched on a dimension the matrix stopped grouping
    // by, it would hand back rows the number on screen never counted.
    // This is THE test that holds `pivotCellRows` and `pivot()` together
    // now that they no longer live in the same file.
    const off: PivotModel = {
      rows: ['company'], columns: ['region'],
      values: [{ key: 'rate', aggFn: 'sum' }],
      disabled: ['region'],
    };
    const drilled = pivotCellRows(DATA, off, COLS, ['PTG'], []);
    expect(drilled.map((r) => r.id)).toEqual([1, 2, 3]);
    // ...and it matches what the matrix printed for that cell.
    const r = pivot(DATA, off, COLS);
    const ptg = r.bodyRows.find((b) => b.label === 'PTG')!;
    expect(drilled.reduce((n, x) => n + Number(x.rate), 0)).toBe(ptg.cells[0]);
  });

  it('the drilled rows re-aggregate to the number on screen', () => {
    const r = pivot(DATA, m, COLS);
    const ptg = r.bodyRows.find((b) => b.label === 'PTG')!;
    const nIdx = r.leafIds.findIndex((l) => splitLeafId(l).colPath[0] === 'N');
    const drilled = pivotCellRows(DATA, m, COLS, ptg.path, ['N']);
    const sum = drilled.reduce((n, x) => n + Number(x.rate), 0);
    expect(sum).toBe(ptg.cells[nIdx]);
  });
});
