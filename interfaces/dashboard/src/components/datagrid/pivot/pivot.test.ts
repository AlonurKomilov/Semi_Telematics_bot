import { describe, it, expect } from 'vitest';

import {
  pivot, isPivotReady, prunePivotModel, bucketOf, pivotToCsvRows,
  pivotCellRows, splitLeafId, insertionIndex,
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
  it('needs a ROW field, and only a row field', () => {
    // Measures are optional: with none you still get the groups and
    // their counts, which is a real answer AND keeps the report on
    // screen while a measure is toggled off to compare.
    expect(pivot(ROWS, model({ rows: [] }), COLUMNS).empty).toBe(true);
    expect(isPivotReady(model({ rows: [] }))).toBe(false);

    const noMeasure = pivot(ROWS, model({ values: [] }), COLUMNS);
    expect(noMeasure.empty).toBe(false);
    expect(noMeasure.bodyRows.map((r) => r.label)).toEqual(['Acme', 'Bolt']);
    // Counts survive; every cell is blank rather than 0.
    expect(noMeasure.bodyRows[0].count).toBeGreaterThan(0);
    expect(noMeasure.bodyRows[0].cells.every((c) => c === null)).toBe(true);
    expect(isPivotReady(model({ values: [] }))).toBe(true);
  });

  it('ignores fields that no longer exist on the grid', () => {
    // A measure naming a dead column is dropped, which leaves the model
    // measure-less — still a renderable report, not an error.
    const r = pivot(ROWS, model({ values: [{ key: 'gone', aggFn: 'sum' }] }), COLUMNS);
    expect(r.empty).toBe(false);
    expect(r.bodyRows[0].cells.every((c) => c === null)).toBe(true);
    // A dead ROW field is different — that IS the one requirement.
    expect(pivot(ROWS, model({ rows: ['gone'] }), COLUMNS).empty).toBe(true);
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
      // Same contract for the on/off list: rebuilding the model field
      // by field is exactly how ``sort`` got lost.
      disabled: [],
      // ...and for every field added since.  This assertion is the guard
      // that has caught all three additions; keep it exhaustive.
      hideEmptyColumns: false,
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
    // Unconfigured now means "no row field" — a measure-less report is
    // a real one (groups + counts) and exports as such.
    expect(pivotToCsvRows(pivot(ROWS, model({ rows: [] }), COLUMNS))).toEqual([]);
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

describe('switching a field OFF without unassigning it', () => {
  // The panel's checkbox used to REMOVE. So unticking a field made it
  // jump back to the unassigned pool — losing its place in the nesting
  // order and, for a measure, its aggregation — and unticking your only
  // measure blanked the report entirely. Off is now a temporary "show
  // me without this", which is what a tick shape actually promises.
  const ROWS = [
    { customer: 'Acme', delivered_at: '2026-01-05', rate: 100 },
    { customer: 'Acme', delivered_at: '2026-02-05', rate: 300 },
    { customer: 'Bolt', delivered_at: '2026-01-09', rate: 50 },
  ];

  it('a switched-off COLUMN dimension collapses the header, keeping the rows', () => {
    const on = pivot(ROWS, model({ columns: ['delivered_at'] }), COLUMNS);
    expect(on.leafIds.length).toBe(2);          // two months

    const off = pivot(
      ROWS,
      { ...model({ columns: ['delivered_at'] }), disabled: ['delivered_at'] },
      COLUMNS,
    );
    // One leaf (just the measure), and the report still renders.
    expect(off.empty).toBe(false);
    expect(off.leafIds.length).toBe(1);
    expect(off.bodyRows.find((r) => r.label === 'Acme')!.cells).toEqual([400]);
  });

  it('a switched-off MEASURE leaves the others rendering', () => {
    const two: PivotModel = {
      rows: ['customer'], columns: [],
      values: [{ key: 'rate', aggFn: 'sum' }, { key: 'rate', aggFn: 'max' }],
      disabled: [],
    };
    expect(pivot(ROWS, two, COLUMNS).leafIds.length).toBe(2);
    // Disabling by KEY switches both entries for that column off — the
    // checkbox is per FIELD, which is what the panel renders.  The
    // report survives with no measures at all: groups and counts stay,
    // so the configuration is still on screen to switch back on.
    const none = pivot(ROWS, { ...two, disabled: ['rate'] }, COLUMNS);
    expect(none.empty).toBe(false);
    expect(none.bodyRows.map((r) => r.label)).toEqual(['Acme', 'Bolt']);
  });

  it('stays ready with every measure off, but not with every ROW off', () => {
    const m = model({ columns: [] });
    expect(isPivotReady(m)).toBe(true);
    expect(isPivotReady({ ...m, disabled: ['rate'] })).toBe(true);
    expect(isPivotReady({ ...m, disabled: ['customer'] })).toBe(false);
  });

  it('survives the prune, and drops only keys the grid lost', () => {
    const pruned = prunePivotModel(
      { ...model({ columns: [] }), disabled: ['customer', 'ghost'] },
      COLUMNS,
    );
    expect(pruned.disabled).toEqual(['customer']);
  });

  it('drill-down ignores a switched-off dimension, like the cells do', () => {
    // If the drill matched on a dimension the matrix stopped grouping
    // by, it would hand back rows the number on screen never counted.
    const m: PivotModel = {
      ...model({ columns: ['delivered_at'] }), disabled: ['delivered_at'],
    };
    const drilled = pivotCellRows(ROWS, m, COLUMNS, ['Acme'], []);
    expect(drilled).toHaveLength(2);
    expect(drilled.reduce((a, r) => a + Number(r.rate), 0)).toBe(400);
  });
});

describe('the off-state never outlives the assignment', () => {
  it('sweeps a disabled entry for a field that is no longer assigned', () => {
    // Switch a field off, then remove it, then re-add it: without this
    // sweep the stale entry survives and the field comes back ALREADY
    // unticked, with nothing on screen explaining why.
    const removed: PivotModel = {
      rows: ['customer'], columns: [],
      values: [{ key: 'rate', aggFn: 'sum' }],
      // 'delivered_at' was switched off and then unassigned.
      disabled: ['delivered_at'],
    };
    expect(prunePivotModel(removed, COLUMNS).disabled).toEqual([]);
  });

  it('keeps the off-state of a field that IS still assigned', () => {
    const kept: PivotModel = {
      rows: ['customer'], columns: ['delivered_at'],
      values: [{ key: 'rate', aggFn: 'sum' }],
      disabled: ['delivered_at'],
    };
    expect(prunePivotModel(kept, COLUMNS).disabled).toEqual(['delivered_at']);
  });
});

describe('the drop lands where the insertion line drew it', () => {
  // Reported from the live panel: rows were [Customer, DEL date,
  // Company]; the line was placed between DEL date and Company; Customer
  // landed AFTER Company. Cause — the line means "insert before the item
  // at this index of the list AS DISPLAYED", but the list still contains
  // the dragged item, and removing it shifts everything after it up one.
  // Only ever wrong dragging DOWNWARD, which is why it survived review.
  const place = (list: string[], key: string, insertBefore: number) => {
    const from = list.indexOf(key);
    const rest = list.filter((k) => k !== key);
    rest.splice(insertionIndex(from, insertBefore), 0, key);
    return rest;
  };

  it('drops a field between the two it was dragged between', () => {
    const rows = ['Customer', 'DEL date', 'Company'];
    // The line sat at Company's top edge → insert before index 2.
    expect(place(rows, 'Customer', 2)).toEqual(['DEL date', 'Customer', 'Company']);
  });

  it('still appends when the line is past the last item', () => {
    const rows = ['Customer', 'DEL date', 'Company'];
    expect(place(rows, 'Customer', 3)).toEqual(['DEL date', 'Company', 'Customer']);
  });

  it('moves upward without an off-by-one', () => {
    const rows = ['Customer', 'DEL date', 'Company'];
    expect(place(rows, 'Company', 1)).toEqual(['Customer', 'Company', 'DEL date']);
    expect(place(rows, 'Company', 0)).toEqual(['Company', 'Customer', 'DEL date']);
  });

  it('treats "before the item just after me" as a no-op', () => {
    const rows = ['Customer', 'DEL date', 'Company'];
    expect(place(rows, 'Customer', 1)).toEqual(rows);
  });

  it('does not shift an index coming from another list', () => {
    // fromIndex -1 = the field isn't in this list, so nothing moves up.
    expect(insertionIndex(-1, 0)).toBe(0);
    expect(insertionIndex(-1, 2)).toBe(2);
  });
});

describe('hideEmptyColumns', () => {
  // A driver appears in a handful of companies, not all of them, so a
  // wide cross-tab is mostly dashes. Pruning is a LEGIBILITY win first.
  const COLS: AnyColumn[] = [
    { key: 'customer', label: 'Customer', pivotable: true },
    { key: 'driver', label: 'Driver', pivotable: true },
    { key: 'rate', label: 'Rate', aggregable: true },
  ];
  const M = (over: Partial<PivotModel> = {}): PivotModel => ({
    rows: ['customer'], columns: ['driver'],
    values: [{ key: 'rate', aggFn: 'sum' }], ...over,
  });
  const ROWS = [
    { customer: 'Acme', driver: 'Ann', rate: 10 },
    { customer: 'Acme', driver: 'Bob', rate: 20 },
    // Cal has a row but NO measure — the column would be all dashes.
    { customer: 'Bolt', driver: 'Cal', rate: null },
  ];

  it('is off by default — an empty column still renders', () => {
    const r = pivot(ROWS, M(), COLS);
    expect(r.headerLevels[0].map((h) => h.label)).toEqual(['Ann', 'Bob', 'Cal']);
    expect(r.hiddenColumns).toBe(0);
  });

  it('drops a bucket whose every cell is empty, and says how many', () => {
    const r = pivot(ROWS, M({ hideEmptyColumns: true }), COLS);
    expect(r.headerLevels[0].map((h) => h.label)).toEqual(['Ann', 'Bob']);
    expect(r.hiddenColumns).toBe(1);
    // The rows themselves are untouched.
    expect(r.bodyRows.map((b) => b.label)).toEqual(['Acme', 'Bolt']);
  });

  it('keeps a bucket that has ROWS but no measure when count is asked for', () => {
    // ``count`` reports the population, so such a column shows a real
    // number — pruning it would delete an answer.
    const r = pivot(
      ROWS,
      M({ hideEmptyColumns: true, values: [{ key: 'rate', aggFn: 'count' }] }),
      COLS,
    );
    expect(r.headerLevels[0].map((h) => h.label)).toEqual(['Ann', 'Bob', 'Cal']);
    expect(r.hiddenColumns).toBe(0);
  });

  it('never prunes to nothing', () => {
    // Every measure missing: pruning would collapse the report to bare
    // row labels with no explanation, which reads as broken.
    const allEmpty = [{ customer: 'Acme', driver: 'Ann', rate: null }];
    const r = pivot(allEmpty, M({ hideEmptyColumns: true }), COLS);
    expect(r.empty).toBe(false);
    expect(r.headerLevels[0].map((h) => h.label)).toEqual(['Ann']);
    expect(r.hiddenColumns).toBe(0);
  });

  it('survives the prune, like every other model field', () => {
    expect(prunePivotModel(M({ hideEmptyColumns: true }), COLS).hideEmptyColumns).toBe(true);
    expect(prunePivotModel(M(), COLS).hideEmptyColumns).toBe(false);
  });

  it('leaves the Total column totalling only what is shown', () => {
    // The Total column re-aggregates the ROW's source rows, so a pruned
    // all-empty bucket contributed nothing to it anyway — the figure must
    // be identical with and without pruning.
    const on = pivot(ROWS, M({ hideEmptyColumns: true }), COLS);
    const off = pivot(ROWS, M(), COLS);
    expect(on.bodyRows.map((b) => b.totals)).toEqual(off.bodyRows.map((b) => b.totals));
    expect(on.grandRowTotal).toEqual(off.grandRowTotal);
  });
});

describe('windowing arithmetic (the part that must not drift)', () => {
  // The renderer's spacer offsets are `from * rowH` and
  // `(total - to) * rowH`. If those two plus the rendered slice don't add
  // up to the full list's height, the scrollbar lies and rows land in the
  // wrong place the further you scroll. Pure arithmetic, so it's testable
  // here rather than in jsdom, which has no layout.
  const windowFor = (
    total: number, bucket: number, perView: number,
    BUCKET = 10, OVERSCAN = 15,
  ) => {
    if (total <= perView + OVERSCAN * 2) {
      return { from: 0, to: total, padTop: 0, padBottom: 0 };
    }
    const maxFrom = Math.max(0, total - perView - OVERSCAN);
    const from = Math.min(maxFrom, Math.max(0, bucket * BUCKET - OVERSCAN));
    const to = Math.min(total, from + perView + OVERSCAN * 2);
    return { from, to, padTop: from, padBottom: total - to };
  };

  it('always accounts for every row', () => {
    for (const bucket of [0, 1, 5, 12, 35, 100]) {
      const w = windowFor(360, bucket, 30);
      const rendered = w.to - w.from;
      expect(w.padTop + rendered + w.padBottom).toBe(360);
    }
  });

  it('renders a bounded slice, not the whole list', () => {
    const w = windowFor(360, 12, 30);
    expect(w.to - w.from).toBeLessThanOrEqual(30 + 15 * 2);
  });

  it('never windows a list that fits — no spacers, no chance to be wrong', () => {
    const w = windowFor(40, 0, 30);
    expect(w).toEqual({ from: 0, to: 40, padTop: 0, padBottom: 0 });
  });

  it('clamps at both ends', () => {
    expect(windowFor(360, 0, 30).from).toBe(0);
    const last = windowFor(360, 100, 30);
    expect(last.to).toBe(360);
    expect(last.padBottom).toBe(0);
  });

  it('never lands past the end of a list that shrank under it', () => {
    // Scroll deep into 360 rows, then collapse every group to 4. The
    // bucket is still ~30; an unclamped window would slice past the end
    // and render nothing behind a tall spacer.
    const w = windowFor(4, 30, 30);
    expect(w.from).toBe(0);
    expect(w.to).toBe(4);
    expect(w.padTop).toBe(0);
    expect(w.padBottom).toBe(0);
  });

  it('keeps overscan ahead of the bucket boundary', () => {
    // A scroll inside one bucket must already have its rows rendered, or
    // you see blank space until the next bucket lands.
    const w = windowFor(360, 3, 30);
    expect(w.from).toBeLessThanOrEqual(3 * 10);
    expect(w.to).toBeGreaterThanOrEqual(3 * 10 + 30);
  });
});

describe('widest candidates (what stops column widths jittering)', () => {
  const COLS: AnyColumn[] = [
    { key: 'customer', label: 'Customer', pivotable: true },
    { key: 'driver', label: 'Driver', pivotable: true },
    { key: 'rate', label: 'Rate', aggregable: true },
  ];
  const ROWS = [
    { customer: 'A', driver: 'Ann', rate: 5 },
    { customer: 'BBBBBBBBBB', driver: 'Ann', rate: 1_000_000 },
    { customer: 'C', driver: 'Ann', rate: 50 },
  ];
  const M = (fn: 'sum' | 'min' | 'avg'): PivotModel => ({
    rows: ['customer'], columns: ['driver'], values: [{ key: 'rate', aggFn: fn }],
  });

  it('is at least as wide as anything that can appear in the column', () => {
    // That is the actual contract — not "the widest body value".  Sizing
    // from a window that only held the `5` row would make the column snap
    // wider the moment 1,000,000 scrolled in; the candidate has to cover
    // every body cell AND the total.
    const r = pivot(ROWS, M('sum'), COLS);
    const candidate = Math.abs(r.leafWidest[0]!);
    for (const row of r.bodyRows) {
      if (row.cells[0] !== null) expect(candidate).toBeGreaterThanOrEqual(Math.abs(row.cells[0]));
    }
    expect(candidate).toBeGreaterThanOrEqual(Math.abs(r.grandTotal[0]!));
  });

  it('beats the grand total when a BODY value is wider — the min case', () => {
    // This is the case the always-present Total row does NOT cover: the
    // grand min is the SMALLEST number, so it is the narrowest string,
    // while a body cell can be far wider.
    const r = pivot(ROWS, M('min'), COLS);
    expect(r.grandTotal).toEqual([5]);
    expect(r.leafWidest).toEqual([1_000_000]);
  });

  it('falls back to the grand total when it is the widest', () => {
    // A sum is at least as wide as any addend, so here the tfoot already
    // dominated — the candidate must not come out NARROWER than it.
    const r = pivot(ROWS, M('sum'), COLS);
    expect(Math.abs(r.leafWidest[0]!)).toBeGreaterThanOrEqual(Math.abs(r.grandTotal[0]!));
  });

  it('picks the row label that renders widest, indent included', () => {
    const r = pivot(ROWS, M('sum'), COLS);
    expect(r.widestRow?.label).toBe('BBBBBBBBBB');
  });

  it('sizes the Total column from the whole report too', () => {
    const r = pivot(ROWS, M('sum'), COLS);
    expect(r.totalWidest).toHaveLength(1);
    expect(Math.abs(r.totalWidest[0]!)).toBeGreaterThanOrEqual(1_000_000);
  });

  it('is defined but valueless when there is nothing to size', () => {
    const r = pivot([], M('sum'), COLS);
    // No column buckets exist, so there are no leaves...
    expect(r.leafWidest).toEqual([]);
    // ...but the Total column still has one slot per value field, and it
    // must be null rather than undefined so renderCell paints a dash.
    expect(r.totalWidest).toEqual([null]);
    expect(r.widestRow).toBeNull();
  });
});
