import { describe, it, expect } from 'vitest';

import { pivot, isPivotReady, prunePivotModel, bucketOf } from './pivot';
import type { PivotModel } from './pivot';
import type { AnyColumn } from '../../../types';

// customer × month, measuring rate — the Loads shape this ships for.
const COLUMNS: AnyColumn[] = [
  { key: 'customer', label: 'Customer', pivotable: true },
  {
    key: 'delivered_at',
    label: 'Delivered',
    pivotable: true,
    // Bucket to a month the way a real column would.
    pivotValue: (r) => String((r as { delivered_at?: string }).delivered_at ?? '').slice(0, 7),
    pivotLabel: (b) => `M:${b}`,
  },
  { key: 'rate', label: 'Rate', aggregable: true },
  { key: 'miles', label: 'Miles', aggregable: true },
];

const ROWS: Record<string, unknown>[] = [
  { customer: 'Acme',  delivered_at: '2026-01-14', rate: 100, miles: 10 },
  { customer: 'Acme',  delivered_at: '2026-01-28', rate: 300, miles: 30 },
  { customer: 'Acme',  delivered_at: '2026-02-03', rate: 50,  miles: 5  },
  { customer: 'Bolt',  delivered_at: '2026-02-11', rate: 200, miles: 20 },
];

const model = (over: Partial<PivotModel> = {}): PivotModel => ({
  rows: ['customer'],
  columns: ['delivered_at'],
  values: [{ key: 'rate', aggFn: 'sum' }],
  ...over,
});

describe('pivot — shape', () => {
  it('builds a header level per dimension, values innermost', () => {
    const r = pivot(ROWS, model(), COLUMNS);
    expect(r.empty).toBe(false);
    expect(r.rowFieldLabel).toBe('Customer');
    // Outer = column buckets (pivotLabel applied), inner = value fields.
    expect(r.headerLevels).toHaveLength(2);
    expect(r.headerLevels[0].map((h) => h.label)).toEqual(['M:2026-01', 'M:2026-02']);
    expect(r.headerLevels[1].map((h) => h.label)).toEqual(['Rate', 'Rate']);
    // The agg fn rides the leaf header — it's the micro-label the footer
    // aggregation already renders.
    expect(r.headerLevels[1].every((h) => h.aggFn === 'sum')).toBe(true);
  });

  it('sorts buckets so months read chronologically', () => {
    const shuffled = [ROWS[3], ROWS[1], ROWS[2], ROWS[0]];
    const r = pivot(shuffled, model(), COLUMNS);
    expect(r.bodyRows.map((b) => b.label)).toEqual(['Acme', 'Bolt']);
    expect(r.headerLevels[0].map((h) => h.label)).toEqual(['M:2026-01', 'M:2026-02']);
  });

  it('spans each column bucket across its value fields', () => {
    const r = pivot(ROWS, model({
      values: [{ key: 'rate', aggFn: 'sum' }, { key: 'miles', aggFn: 'avg' }],
    }), COLUMNS);
    expect(r.headerLevels[0].map((h) => h.span)).toEqual([2, 2]);
    expect(r.leafIds).toHaveLength(4);
    expect(r.headerLevels[1].map((h) => h.aggFn)).toEqual(['sum', 'avg', 'sum', 'avg']);
  });

  it('emits ONE header level when there is no column field', () => {
    const r = pivot(ROWS, model({ columns: [] }), COLUMNS);
    expect(r.headerLevels).toHaveLength(1);
    expect(r.leafIds).toHaveLength(1);
    expect(r.bodyRows.find((b) => b.label === 'Acme')!.cells[0]).toBe(450);
  });
});

describe('pivot — numbers', () => {
  it('aggregates each (row × column) intersection', () => {
    const r = pivot(ROWS, model(), COLUMNS);
    const acme = r.bodyRows.find((b) => b.label === 'Acme')!;
    const bolt = r.bodyRows.find((b) => b.label === 'Bolt')!;
    expect(acme.cells).toEqual([400, 50]);    // Jan 100+300, Feb 50
    expect(bolt.cells).toEqual([null, 200]);  // no January
  });

  it('renders an empty intersection as null, never 0', () => {
    // A real measured zero and "nothing here" must not look the same.
    const r = pivot(ROWS, model(), COLUMNS);
    expect(r.bodyRows.find((b) => b.label === 'Bolt')!.cells[0]).toBeNull();
  });

  it('counts source rows per bucket for the "(n)" badge', () => {
    const r = pivot(ROWS, model(), COLUMNS);
    expect(r.bodyRows.find((b) => b.label === 'Acme')!.count).toBe(3);
    expect(r.bodyRows.find((b) => b.label === 'Bolt')!.count).toBe(1);
  });

  it('totals each leaf column across every row', () => {
    const r = pivot(ROWS, model(), COLUMNS);
    expect(r.grandTotal).toEqual([400, 250]);   // Jan 400, Feb 50+200
  });

  it('averages over CONTRIBUTING values only', () => {
    const r = pivot(ROWS, model({
      columns: [], values: [{ key: 'rate', aggFn: 'avg' }],
    }), COLUMNS);
    expect(r.bodyRows.find((b) => b.label === 'Acme')!.cells[0]).toBe(150); // 450/3
  });

  it('excludes a missing numeric instead of folding it in as 0', () => {
    const withGap = [...ROWS, { customer: 'Acme', delivered_at: '2026-01-20', rate: null }];
    const r = pivot(withGap, model({
      columns: [], values: [{ key: 'rate', aggFn: 'avg' }],
    }), COLUMNS);
    // Still 450/3 — a null rate must not drag the average to 112.5.
    expect(r.bodyRows.find((b) => b.label === 'Acme')!.cells[0]).toBe(150);
  });

  it('counts the CELL population, not the row-bucket total', () => {
    const r = pivot(ROWS, model({ values: [{ key: 'rate', aggFn: 'count' }] }), COLUMNS);
    const acme = r.bodyRows.find((b) => b.label === 'Acme')!;
    // 2 in January, 1 in February — NOT 3 repeated across both columns.
    expect(acme.cells).toEqual([2, 1]);
  });

  it('uses aggValue when the cell renders something formatted', () => {
    const cols: AnyColumn[] = [
      { key: 'customer', label: 'Customer', pivotable: true },
      { key: 'pretty', label: 'Total', aggregable: true, aggValue: (r) => Number((r as { cents?: number }).cents ?? 0) / 100 },
    ];
    const r = pivot(
      [{ customer: 'Acme', pretty: '$1.50', cents: 150 }],
      { rows: ['customer'], columns: [], values: [{ key: 'pretty', aggFn: 'sum' }] },
      cols,
    );
    expect(r.bodyRows[0].cells[0]).toBe(1.5);
  });
});

describe('pivot — guards', () => {
  it('is empty without a row field or without a measure', () => {
    expect(pivot(ROWS, model({ rows: [] }), COLUMNS).empty).toBe(true);
    expect(pivot(ROWS, model({ values: [] }), COLUMNS).empty).toBe(true);
    expect(isPivotReady(model({ rows: [] }))).toBe(false);
    expect(isPivotReady(model())).toBe(true);
  });

  it('ignores fields that no longer exist on the grid', () => {
    const r = pivot(ROWS, model({ values: [{ key: 'gone', aggFn: 'sum' }] }), COLUMNS);
    expect(r.empty).toBe(true);
  });

  it('prunes a stale saved model against live columns', () => {
    const pruned = prunePivotModel(
      { rows: ['customer', 'gone'], columns: ['nope'], values: [{ key: 'rate', aggFn: 'sum' }, { key: 'x', aggFn: 'avg' }] },
      COLUMNS,
    );
    expect(pruned).toEqual({
      rows: ['customer'], columns: [], values: [{ key: 'rate', aggFn: 'sum' }],
    });
  });

  it('labels a blank bucket rather than rendering an empty header', () => {
    const r = pivot([{ customer: '', rate: 5 }], model({ columns: [] }), COLUMNS);
    expect(r.bodyRows[0].label).toBe('—');
  });

  it('falls back through pivotValue → filterValue → raw cell', () => {
    const raw: AnyColumn = { key: 'a', label: 'A' };
    const filtered: AnyColumn = { key: 'a', label: 'A', filterValue: () => 'F' };
    const pivoted: AnyColumn = { key: 'a', label: 'A', filterValue: () => 'F', pivotValue: () => 'P' };
    expect(bucketOf({ a: 'R' }, raw)).toBe('R');
    expect(bucketOf({ a: 'R' }, filtered)).toBe('F');
    expect(bucketOf({ a: 'R' }, pivoted)).toBe('P');
  });

  it('handles an empty row set without throwing', () => {
    const r = pivot([], model(), COLUMNS);
    expect(r.bodyRows).toEqual([]);
    expect(r.empty).toBe(false);
  });
});
