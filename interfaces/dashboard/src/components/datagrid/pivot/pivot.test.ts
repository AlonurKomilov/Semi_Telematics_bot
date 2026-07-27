import { describe, it, expect } from 'vitest';

import { pivot, isPivotReady, prunePivotModel, bucketOf, pivotToCsvRows } from './pivot';
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

describe('pivot — multi-level columns (Region > Quarter)', () => {
  const COLS: AnyColumn[] = [
    { key: 'product', label: 'Product', pivotable: true },
    { key: 'region', label: 'Region', pivotable: true },
    { key: 'quarter', label: 'Quarter', pivotable: true },
    { key: 'sales', label: 'Sales', aggregable: true },
  ];
  const DATA = [
    { product: 'Apples', region: 'North', quarter: 'Q1', sales: 1000 },
    { product: 'Apples', region: 'North', quarter: 'Q2', sales: 1100 },
    { product: 'Apples', region: 'South', quarter: 'Q1', sales: 1200 },
    { product: 'Oranges', region: 'North', quarter: 'Q1', sales: 800 },
  ];
  const twoLevel: PivotModel = {
    rows: ['product'],
    columns: ['region', 'quarter'],
    values: [{ key: 'sales', aggFn: 'sum' }],
  };

  it('emits one header level per column dimension, plus the value level', () => {
    const r = pivot(DATA, twoLevel, COLS);
    expect(r.headerLevels).toHaveLength(3);
    expect(r.headerLevels[0].map((h) => h.label)).toEqual(['North', 'South']);
    expect(r.headerLevels[1].map((h) => h.label)).toEqual(['Q1', 'Q2', 'Q1']);
    expect(r.headerLevels[2].every((h) => h.aggFn === 'sum')).toBe(true);
  });

  it('spans a parent across exactly its own children', () => {
    const r = pivot(DATA, twoLevel, COLS);
    // North covers Q1+Q2 (2 leaves), South only Q1 (1 leaf).
    expect(r.headerLevels[0].map((h) => h.span)).toEqual([2, 1]);
    expect(r.headerLevels[1].map((h) => h.span)).toEqual([1, 1, 1]);
    expect(r.leafIds).toHaveLength(3);
  });

  it('spans parents across MULTIPLE value fields too', () => {
    const r = pivot(DATA, {
      ...twoLevel,
      values: [{ key: 'sales', aggFn: 'sum' }, { key: 'sales', aggFn: 'avg' }],
    }, COLS);
    // North (2 quarters) x 2 values = 4 leaves beneath it.
    expect(r.headerLevels[0].map((h) => h.span)).toEqual([4, 2]);
    expect(r.leafIds).toHaveLength(6);
  });

  it('aggregates into the right (row x path) cell', () => {
    const r = pivot(DATA, twoLevel, COLS);
    const apples = r.bodyRows.find((b) => b.label === 'Apples')!;
    const oranges = r.bodyRows.find((b) => b.label === 'Oranges')!;
    // leaves: North/Q1, North/Q2, South/Q1
    expect(apples.cells).toEqual([1000, 1100, 1200]);
    expect(oranges.cells).toEqual([800, null, null]);
    expect(r.grandTotal).toEqual([1800, 1100, 1200]);
  });

  it('keeps a parent contiguous even when the source rows are shuffled', () => {
    const shuffled = [DATA[2], DATA[0], DATA[3], DATA[1]];
    const r = pivot(shuffled, twoLevel, COLS);
    expect(r.headerLevels[0].map((h) => h.label)).toEqual(['North', 'South']);
    expect(r.headerLevels[0].map((h) => h.span)).toEqual([2, 1]);
  });
});

describe('pivot — CSV export matches what is on screen', () => {
  it('flattens nested headers into one unambiguous name per column', () => {
    const COLS: AnyColumn[] = [
      { key: 'product', label: 'Product', pivotable: true },
      { key: 'region', label: 'Region', pivotable: true },
      { key: 'sales', label: 'Sales', aggregable: true },
    ];
    const grid = pivotToCsvRows(pivot(
      [{ product: 'Apples', region: 'North', sales: 10 }],
      { rows: ['product'], columns: ['region'], values: [{ key: 'sales', aggFn: 'sum' }] },
      COLS,
    ));
    expect(grid[0]).toEqual(['Product', 'Rows', 'North / Sales (sum)']);
  });

  it('keeps two measures on one bucket distinguishable by their agg fn', () => {
    const grid = pivotToCsvRows(pivot(ROWS, model({
      columns: [],
      values: [{ key: 'rate', aggFn: 'sum' }, { key: 'rate', aggFn: 'avg' }],
    }), COLUMNS));
    // Without the fn suffix these would be two identical "Rate" columns.
    expect(grid[0]).toEqual(['Customer', 'Rows', 'Rate (sum)', 'Rate (avg)']);
  });

  it('emits raw numbers and a total row, with empties left EMPTY', () => {
    const grid = pivotToCsvRows(pivot(ROWS, model(), COLUMNS));
    expect(grid[1]).toEqual(['Acme', '3', '400', '50']);
    // Bolt has no January — blank, not 0, exactly like the dash on screen.
    expect(grid[2]).toEqual(['Bolt', '1', '', '200']);
    expect(grid[grid.length - 1]).toEqual(['Total', '4', '400', '250']);
  });

  it('exports nothing when the pivot is not configured', () => {
    expect(pivotToCsvRows(pivot(ROWS, model({ values: [] }), COLUMNS))).toEqual([]);
  });
});
