// ── Pivot — the pure transform ───────────────────────────────────────
//
// Turns a flat row list into a cross-tab MATRIX: one body row per value
// of the ROW field, one leaf column per (COLUMN-field value × value
// field), each cell an aggregate.
//
// A pivoted grid is a REPORT, not a record list — so this deliberately
// emits an explicit matrix (header levels, leaf ids, body rows, grand
// total) rather than synthesizing ``AnyColumn[]`` for the interactive
// grid.  Pinning / resize / selection / per-column menus are meaningless
// on synthesized aggregate rows; keeping them out of the output is what
// stops pivot leaking conditionals into DataGrid.tsx.
//
// Framework-free and unit-tested, like ``datagrid/aggregation.ts`` — and
// it REUSES that module rather than re-implementing reduction.  The
// null-handling there is hard-won (a missing numeric must be excluded,
// never folded in as 0; a missing date must not become 1970).
//
// PHASE 1 shape: at most ONE row field and ONE column field, N value
// fields.  ``PivotModel`` still carries ARRAYS so multi-level pivoting
// can arrive later without changing the persisted key's shape.

import type { AnyColumn, AggFn } from '../../../types';
import { computeAggregate, toAggNumber } from '../aggregation';

/** One measure: which column to reduce, and how. */
export interface PivotValueField {
  key: string;
  aggFn: AggFn;
}

/** What the operator configured in the panel.  Arrays are future-proofing
 *  for multi-level; Phase 1 reads at most the first entry of rows/columns. */
export interface PivotModel {
  rows: string[];
  columns: string[];
  values: PivotValueField[];
}

/** One header cell — ``span`` is a colSpan over the leaf columns below. */
export interface PivotHeaderCell {
  label: string;
  span: number;
  /** Present on the LEAF level only: the agg fn, rendered as the micro
   *  label under the header (matching the footer-aggregation treatment). */
  aggFn?: AggFn;
}

export interface PivotBodyRow {
  /** Raw bucket value — stable identity for React keys. */
  key: string;
  /** What the operator reads (``pivotLabel`` applied, or the raw value). */
  label: string;
  /** How many source rows fell in this bucket — the "Apples (4)" count. */
  count: number;
  /** One entry per leaf column, in ``leafIds`` order.  ``null`` = nothing
   *  to aggregate (an empty intersection), rendered as a dash — NOT 0,
   *  which would read as a real measured zero. */
  cells: (number | null)[];
}

export interface PivotResult {
  /** Header rows, outermost first.  Phase 1 emits 1 level when there is
   *  no column field (just the value names), else 2. */
  headerLevels: PivotHeaderCell[][];
  /** Leaf column ids, left to right — ``<columnBucket>||<valueKey>``. */
  leafIds: string[];
  /** The value-field key behind each leaf, for per-cell formatting. */
  leafValueKeys: string[];
  /** Label for the top-left corner cell (the row field's name). */
  rowFieldLabel: string;
  bodyRows: PivotBodyRow[];
  /** Same shape as a body row's cells — the bottom accent row. */
  grandTotal: (number | null)[];
  /** True when the model can't produce a table yet (no value field, or no
   *  row field).  Callers show the "choose fields" empty state. */
  empty: boolean;
}

const EMPTY_LABEL = '—';

/** The bucket a row falls into for a given field.
 *
 *  Accessor precedence mirrors the rest of the grid: an explicit
 *  ``pivotValue`` wins (that's how a date column becomes a MONTH), then
 *  the filter accessor (already written for many columns), then the raw
 *  cell.  Always a string — buckets are map keys. */
export function bucketOf(row: Record<string, unknown>, col: AnyColumn): string {
  if (col.pivotValue) return String(col.pivotValue(row) ?? '');
  if (col.filterValue) return String(col.filterValue(row) ?? '');
  return String(row[col.key] ?? '');
}

/** Human label for a bucket ('' → an em dash, so a blank never looks
 *  like a rendering bug). */
function labelOf(bucket: string, col: AnyColumn): string {
  if (bucket === '') return EMPTY_LABEL;
  return col.pivotLabel ? col.pivotLabel(bucket) : bucket;
}

/** The number a value field contributes for one row.  ``aggValue`` is the
 *  same escape hatch footer aggregation uses (when the cell renders
 *  "$2,847" but the true number lives elsewhere on the row). */
function measureOf(row: Record<string, unknown>, col: AnyColumn): number {
  const raw = col.aggValue ? col.aggValue(row) : row[col.key];
  return toAggNumber(raw);
}

const byKey = (columns: AnyColumn[]) =>
  new Map(columns.map((c) => [c.key, c]));

/**
 * Build the matrix.  ``rows`` must be the SAME post-filter/search/segment
 * set the grid's own footer aggregation reduces, so the two can never
 * disagree.
 */
export function pivot(
  rows: Record<string, unknown>[],
  model: PivotModel,
  columns: AnyColumn[],
): PivotResult {
  const cols = byKey(columns);
  const rowCol = model.rows[0] ? cols.get(model.rows[0]) : undefined;
  const colCol = model.columns[0] ? cols.get(model.columns[0]) : undefined;
  const valueFields = model.values.filter((v) => cols.has(v.key));

  const blank: PivotResult = {
    headerLevels: [], leafIds: [], leafValueKeys: [],
    rowFieldLabel: '', bodyRows: [], grandTotal: [], empty: true,
  };
  // A pivot needs something to break down BY and something to measure.
  if (!rowCol || valueFields.length === 0) return blank;

  // ── Buckets ────────────────────────────────────────────────────────
  // Sorted so the output is deterministic (and so 'YYYY-MM' month
  // buckets fall in chronological order for free).
  const rowBuckets: string[] = [];
  const colBuckets: string[] = [];
  const seenRow = new Set<string>();
  const seenCol = new Set<string>();
  for (const r of rows) {
    const rb = bucketOf(r, rowCol);
    if (!seenRow.has(rb)) { seenRow.add(rb); rowBuckets.push(rb); }
    if (colCol) {
      const cb = bucketOf(r, colCol);
      if (!seenCol.has(cb)) { seenCol.add(cb); colBuckets.push(cb); }
    }
  }
  rowBuckets.sort();
  colBuckets.sort();
  // No column field → a single implicit bucket, so the value fields
  // still render as leaves.
  const effectiveColBuckets = colCol ? colBuckets : [''];

  // ── Leaves ─────────────────────────────────────────────────────────
  const leafIds: string[] = [];
  const leafValueKeys: string[] = [];
  for (const cb of effectiveColBuckets) {
    for (const v of valueFields) {
      leafIds.push(`${cb}||${v.key}`);
      leafValueKeys.push(v.key);
    }
  }

  // ── Headers ────────────────────────────────────────────────────────
  const valueLevel: PivotHeaderCell[] = [];
  for (const cb of effectiveColBuckets) {
    for (const v of valueFields) {
      valueLevel.push({
        label: cols.get(v.key)?.label ?? v.key,
        span: 1,
        aggFn: v.aggFn,
      });
    }
  }
  const headerLevels: PivotHeaderCell[][] = colCol
    ? [
        // Outer level: one spanning cell per column bucket.
        effectiveColBuckets.map((cb) => ({
          label: labelOf(cb, colCol),
          span: valueFields.length,
        })),
        valueLevel,
      ]
    : [valueLevel];

  // ── Cells ──────────────────────────────────────────────────────────
  // Collect the contributing numbers per (rowBucket, leaf), then reduce
  // ONCE through the shared engine.  Collecting first (rather than
  // running totals) is what lets 'avg' and 'count' be correct.
  const push = (m: Map<string, number[]>, k: string, n: number) => {
    const arr = m.get(k);
    if (arr) arr.push(n); else m.set(k, [n]);
  };
  const bump = (m: Map<string, number>, k: string) =>
    m.set(k, (m.get(k) ?? 0) + 1);

  const collected = new Map<string, number[]>();   // `${rb} ${leaf}`
  const totals = new Map<string, number[]>();      // leaf
  const rowCounts = new Map<string, number>();     // rb — the "(4)" badge
  const cellCounts = new Map<string, number>();    // `${rb} ${cb}`
  const colCounts = new Map<string, number>();     // cb
  let totalRowCount = 0;

  for (const r of rows) {
    const rb = bucketOf(r, rowCol);
    const cb = colCol ? bucketOf(r, colCol) : '';
    bump(rowCounts, rb);
    bump(cellCounts, `${rb} ${cb}`);
    bump(colCounts, cb);
    totalRowCount += 1;
    for (const v of valueFields) {
      const n = measureOf(r, cols.get(v.key)!);
      // NaN = missing / non-numeric.  Skipped rather than pushed, so
      // sum/avg/min/max never see a phantom 0 (aggregation.ts's rule).
      if (!Number.isFinite(n)) continue;
      const leaf = `${cb}||${v.key}`;
      push(collected, `${rb} ${leaf}`, n);
      push(totals, leaf, n);
    }
  }

  // ``count`` reports the ROW COUNT it is handed, so each cell must pass
  // its OWN population — the rows in that (row bucket x column bucket)
  // intersection.  Passing the row-bucket total would repeat one number
  // across every column, which reads as a bug.
  const reduce = (fn: AggFn, values: number[] | undefined, rowCount: number) => {
    if (fn === 'count') return rowCount === 0 ? null : rowCount;
    return values && values.length ? computeAggregate(fn, values, rowCount) : null;
  };
  const colOf = (leaf: string) => leaf.slice(0, leaf.lastIndexOf('||'));

  const bodyRows: PivotBodyRow[] = rowBuckets.map((rb) => ({
    key: rb,
    label: labelOf(rb, rowCol),
    count: rowCounts.get(rb) ?? 0,
    cells: leafIds.map((leaf, i) => {
      const v = valueFields.find((f) => f.key === leafValueKeys[i])!;
      return reduce(
        v.aggFn,
        collected.get(`${rb} ${leaf}`),
        cellCounts.get(`${rb} ${colOf(leaf)}`) ?? 0,
      );
    }),
  }));

  const grandTotal = leafIds.map((leaf, i) => {
    const v = valueFields.find((f) => f.key === leafValueKeys[i])!;
    return reduce(v.aggFn, totals.get(leaf), colCounts.get(colOf(leaf)) ?? totalRowCount);
  });

  return {
    headerLevels,
    leafIds,
    leafValueKeys,
    rowFieldLabel: rowCol.label,
    bodyRows,
    grandTotal,
    empty: false,
  };
}

/** Is this model renderable?  The panel uses it to decide between the
 *  matrix and the "pick a field" hint. */
export function isPivotReady(model: PivotModel): boolean {
  return model.rows.length > 0 && model.values.length > 0;
}

/** Drop fields that no longer exist on the grid (a column was removed or
 *  renamed since the model was saved) — the same staleness rule saved
 *  tabs apply to their filters. */
export function prunePivotModel(model: PivotModel, columns: AnyColumn[]): PivotModel {
  const keys = new Set(columns.map((c) => c.key));
  return {
    rows: model.rows.filter((k) => keys.has(k)),
    columns: model.columns.filter((k) => keys.has(k)),
    values: model.values.filter((v) => keys.has(v.key)),
  };
}
