import { describe, it, expect } from 'vitest';

import {
  pivot, isPivotReady, prunePivotModel, bucketOf, pivotToCsvRows,
  pivotCellRows, splitLeafId,
} from './pivot';
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
  values: [{ key: 'rate', aggFn: 'sum' as const }],
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
      values: [{ key: 'rate', aggFn: 'sum' as const }, { key: 'miles', aggFn: 'avg' }],
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
      { rows: ['customer', 'gone'], columns: ['nope'], values: [{ key: 'rate', aggFn: 'sum' as const }, { key: 'x', aggFn: 'avg' }] },
      COLUMNS,
    );
    expect(pruned).toEqual({
      rows: ['customer'], columns: [], values: [{ key: 'rate', aggFn: 'sum' as const }],
      // ``sort`` is now carried through — dropping it silently killed
      // pivot sorting in the product.  Null here because this model
      // never had one.
      sort: null,
    });
  });

  it('gives a blank bucket WORDS, not the empty-cell dash', () => {
    // It used to be '—', the exact glyph the view paints in an empty
    // intersection — so a real category (the no-driver column) and "no
    // number here" were indistinguishable.  A category always gets a
    // label a person can read.
    const r = pivot([{ customer: '', rate: 5 }], model({ columns: [] }), COLUMNS);
    expect(r.bodyRows[0].label).toBe('(none)');
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
      values: [{ key: 'rate', aggFn: 'sum' as const }, { key: 'rate', aggFn: 'avg' }],
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

describe('pivot — multi-level ROWS (Company > Customer)', () => {
  const COLS: AnyColumn[] = [
    { key: 'company', label: 'Company', pivotable: true },
    { key: 'customer', label: 'Customer', pivotable: true },
    { key: 'rate', label: 'Rate', aggregable: true },
  ];
  const DATA = [
    { company: 'PTG', customer: 'Acme', rate: 100 },
    { company: 'PTG', customer: 'Acme', rate: 200 },
    { company: 'PTG', customer: 'Bolt', rate: 50 },
    { company: 'CFT', customer: 'Zed', rate: 10 },
  ];
  const nested: PivotModel = {
    rows: ['company', 'customer'],
    columns: [],
    values: [{ key: 'rate', aggFn: 'sum' as const }],
  };

  it('emits a row per LEVEL, parents before their children', () => {
    const r = pivot(DATA, nested, COLS);
    expect(r.bodyRows.map((b) => `${b.depth}:${b.label}`)).toEqual([
      '0:CFT', '1:Zed', '0:PTG', '1:Acme', '1:Bolt',
    ]);
  });

  it('gives a parent a REAL total, not a blank', () => {
    const r = pivot(DATA, nested, COLS);
    const ptg = r.bodyRows.find((b) => b.depth === 0 && b.label === 'PTG')!;
    // 100 + 200 + 50 — a collapsed parent must still answer the question.
    expect(ptg.cells[0]).toBe(350);
    expect(ptg.count).toBe(3);
    expect(r.bodyRows.find((b) => b.label === 'Acme')!.cells[0]).toBe(300);
  });

  it('marks which rows have children so the view can draw a chevron', () => {
    const r = pivot(DATA, nested, COLS);
    expect(r.bodyRows.find((b) => b.label === 'PTG')!.hasChildren).toBe(true);
    expect(r.bodyRows.find((b) => b.label === 'Acme')!.hasChildren).toBe(false);
  });

  it('carries the full path so the view can hide orphans of a collapsed parent', () => {
    const r = pivot(DATA, nested, COLS);
    expect(r.bodyRows.find((b) => b.label === 'Acme')!.path).toEqual(['PTG', 'Acme']);
    expect(r.bodyRows.find((b) => b.label === 'PTG')!.path).toEqual(['PTG']);
  });

  it('nests rows and columns at the same time', () => {
    const r = pivot(
      [
        { company: 'PTG', customer: 'Acme', region: 'N', rate: 100 },
        { company: 'PTG', customer: 'Acme', region: 'S', rate: 200 },
      ],
      { ...nested, columns: ['region'] },
      [...COLS, { key: 'region', label: 'Region', pivotable: true }],
    );
    expect(r.headerLevels[0].map((h) => h.label)).toEqual(['N', 'S']);
    expect(r.bodyRows.find((b) => b.label === 'PTG')!.cells).toEqual([100, 200]);
  });

  it('names the corner cell after every row dimension', () => {
    expect(pivot(DATA, nested, COLS).rowFieldLabel).toBe('Company / Customer');
  });

  it('still behaves exactly as before with ONE row dimension', () => {
    const r = pivot(DATA, { ...nested, rows: ['company'] }, COLS);
    expect(r.bodyRows.every((b) => b.depth === 0 && !b.hasChildren)).toBe(true);
    expect(r.bodyRows.map((b) => b.label)).toEqual(['CFT', 'PTG']);
  });
});

describe('pivot — sorting by a measure', () => {
  const COLS: AnyColumn[] = [
    { key: 'company', label: 'Company', pivotable: true },
    { key: 'customer', label: 'Customer', pivotable: true },
    { key: 'rate', label: 'Rate', aggregable: true },
  ];
  const DATA = [
    { company: 'PTG', customer: 'Acme', rate: 100 },
    { company: 'PTG', customer: 'Zed', rate: 900 },
    { company: 'CFT', customer: 'Bolt', rate: 500 },
  ];
  const base: PivotModel = {
    rows: ['company'], columns: [], values: [{ key: 'rate', aggFn: 'sum' as const }],
  };
  const leaf = '||rate';

  it('orders alphabetically when no sort is set', () => {
    expect(pivot(DATA, base, COLS).bodyRows.map((b) => b.label))
      .toEqual(['CFT', 'PTG']);
  });

  it('orders by the measure, descending', () => {
    const r = pivot(DATA, { ...base, sort: { leaf, dir: 'desc' } }, COLS);
    // PTG 1000 > CFT 500
    expect(r.bodyRows.map((b) => b.label)).toEqual(['PTG', 'CFT']);
  });

  it('orders by the measure, ascending', () => {
    const r = pivot(DATA, { ...base, sort: { leaf, dir: 'asc' } }, COLS);
    expect(r.bodyRows.map((b) => b.label)).toEqual(['CFT', 'PTG']);
  });

  it('sorts siblings WITHIN a parent, never tearing the tree apart', () => {
    const r = pivot(DATA, {
      rows: ['company', 'customer'], columns: [],
      values: [{ key: 'rate', aggFn: 'sum' as const }],
      sort: { leaf, dir: 'desc' },
    }, COLS);
    // PTG (1000) before CFT (500); inside PTG, Zed (900) before Acme (100).
    expect(r.bodyRows.map((b) => `${b.depth}:${b.label}`)).toEqual([
      '0:PTG', '1:Zed', '1:Acme', '0:CFT', '1:Bolt',
    ]);
  });

  it('sinks rows with no value in that column to the bottom either way', () => {
    const data = [
      { company: 'PTG', region: 'N', rate: 10 },
      { company: 'CFT', region: 'S', rate: 20 },
    ];
    const cols = [...COLS, { key: 'region', label: 'Region', pivotable: true }];
    const m = (dir: 'asc' | 'desc'): PivotModel => ({
      rows: ['company'], columns: ['region'],
      values: [{ key: 'rate', aggFn: 'sum' as const }],
      sort: { leaf: `N||rate`, dir },
    });
    // CFT has nothing in N — absent is not "smaller than every number".
    expect(pivot(data, m('asc'), cols).bodyRows.map((b) => b.label)).toEqual(['PTG', 'CFT']);
    expect(pivot(data, m('desc'), cols).bodyRows.map((b) => b.label)).toEqual(['PTG', 'CFT']);
  });

  it('falls back to label order when the sorted leaf no longer exists', () => {
    const r = pivot(DATA, { ...base, sort: { leaf: 'gone||rate', dir: 'desc' } }, COLS);
    expect(r.bodyRows.map((b) => b.label)).toEqual(['CFT', 'PTG']);
  });
});

describe('pivot — drill-down', () => {
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

  it('the drilled rows re-aggregate to the number on screen', () => {
    const r = pivot(DATA, m, COLS);
    const ptg = r.bodyRows.find((b) => b.label === 'PTG')!;
    const nIdx = r.leafIds.findIndex((l) => splitLeafId(l).colPath[0] === 'N');
    const drilled = pivotCellRows(DATA, m, COLS, ptg.path, ['N']);
    const sum = drilled.reduce((n, x) => n + Number(x.rate), 0);
    expect(sum).toBe(ptg.cells[nIdx]);
  });
});

describe('the model survives a round-trip through the grid', () => {
  // The bug this pins: DataGrid rebuilt the stored model field-by-field
  // and prunePivotModel rebuilt it again, and NEITHER carried ``sort``.
  // So every header click wrote a sort that the next render discarded —
  // the rows never reordered, the caret never appeared, and the feature
  // was dead in the product while every test here passed, because they
  // all call pivot() directly and never go through the persistence path.
  it('prunePivotModel keeps the sort', () => {
    const model: PivotModel = {
      rows: ['customer'], columns: [], values: [{ key: 'rate', aggFn: 'sum' as const }],
      sort: { leaf: '||rate', dir: 'desc' },
    };
    const pruned = prunePivotModel(model, COLUMNS);
    expect(pruned.sort).toEqual({ leaf: '||rate', dir: 'desc' });
  });

  it('keeps a sort whose leaf is gone rather than discarding the report', () => {
    // pivot() falls back to label order for an unknown leaf — that is a
    // RENDERING decision.  Throwing the choice away at the storage layer
    // would lose it permanently the moment a column was briefly hidden.
    const model: PivotModel = {
      rows: ['customer'], columns: [], values: [{ key: 'rate', aggFn: 'sum' as const }],
      sort: { leaf: 'gone||nope', dir: 'asc' },
    };
    expect(prunePivotModel(model, COLUMNS).sort).toEqual({ leaf: 'gone||nope', dir: 'asc' });
  });
});
