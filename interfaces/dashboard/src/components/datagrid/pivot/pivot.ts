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
  /** Order rows by a MEASURE instead of alphabetically — "biggest
   *  customer first".  ``leaf`` is a leaf column id; a stale one (the
   *  bucket vanished after a filter change) silently falls back to the
   *  label order rather than throwing the report away. */
  sort?: { leaf: string; dir: 'asc' | 'desc' } | null;
  /** Assigned fields that are currently switched OFF.  The checkbox in
   *  the panel toggles membership here; it does NOT unassign — a field
   *  you untick stays where you put it (with its aggregation, its
   *  position in the nesting order) and simply stops contributing, so
   *  you can compare "with and without" without rebuilding the report.
   *  OPTIONAL and additive on purpose: a model saved before this existed
   *  has no such list, which reads correctly as "nothing is off". */
  disabled?: string[];
  /** Drop column buckets that hold nothing.  On a wide report most
   *  intersections are empty — a driver appears in a handful of
   *  companies, not all of them — so a 61-column matrix can be ~90%
   *  dashes.  Pruning them is a LEGIBILITY win first (what remains is
   *  the data) and a size win second.  Off by default: silently
   *  removing columns would be worse than showing empty ones, so the
   *  view reports how many it hid. */
  hideEmptyColumns?: boolean;
  /** Freeze the row-label column against the left edge.  ONE setting for
   *  the whole column, not per field: the renderer draws a single merged
   *  label cell ("Company / Customer"), so a per-field pin would silently
   *  govern its neighbours.
   *
   *  Default OFF (owner's call, and MUI's behaviour): a frozen column
   *  costs real width on every report, and most reports are narrow enough
   *  not to need it.  Turn it on from ROWS when a wide matrix starts
   *  scrolling the identity out of sight. */
  pinRowLabels?: boolean;
  /** Freeze the Total column against the right edge.  Lives with VALUES
   *  because the Total column is GENERATED from them (one per value
   *  field) — it is not a field and so has no field row of its own. */
  pinTotals?: boolean;
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
  /** Full path id — stable identity for React keys AND the expand key. */
  key: string;
  /** One bucket per row dimension, outermost first. */
  path: string[];
  /** 0 = a top-level group.  Drives indentation. */
  depth: number;
  /** Whether anything sits beneath it (so the view knows to draw a
   *  chevron).  A leaf at the deepest level has none. */
  hasChildren: boolean;
  /** What the operator reads (``pivotLabel`` applied, or the raw value). */
  label: string;
  /** How many source rows fell in this bucket — the "Apples (4)" count. */
  count: number;
  /** One entry per leaf column, in ``leafIds`` order.  ``null`` = nothing
   *  to aggregate (an empty intersection), rendered as a dash — NOT 0,
   *  which would read as a real measured zero. */
  cells: (number | null)[];
  /** One entry per VALUE FIELD — this row's figure across every column
   *  bucket.  Re-aggregated from the row's own source rows, never summed
   *  from ``cells``: adding up per-column averages would be arithmetic
   *  nonsense, and ``count`` would multiply. Empty when the model has no
   *  column dimension (the leaf columns are already the totals). */
  totals: (number | null)[];
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
  /** One per value field — the bottom-right corner, where the Total row
   *  meets the Total column.  Empty alongside ``PivotBodyRow.totals``. */
  grandRowTotal: (number | null)[];
  /** Labels for the Total column group, one per value field.  Empty when
   *  there is no column dimension to total ACROSS. */
  totalLabels: string[];
  /** How many column buckets ``hideEmptyColumns`` removed.  Reported so
   *  the surface can SAY so — a matrix quietly missing columns is worse
   *  than one showing empty ones. */
  hiddenColumns: number;
  /** Per leaf, the value most likely to render WIDEST — the greatest
   *  magnitude across every body row, and the grand total.  Once rows are
   *  windowed, column widths would otherwise be decided by whichever
   *  rows happen to be in the DOM, so they'd shift as you scroll.  This
   *  lets the view size columns from the whole REPORT instead: width
   *  becomes a function of the data, not of scroll position. */
  leafWidest: (number | null)[];
  /** Same, for the pinned Total column. */
  totalWidest: (number | null)[];
  /** The body row whose label will render widest — longest label at the
   *  deepest indent.  Sizes the frozen row-label column. */
  widestRow: { label: string; depth: number; count: number } | null;
  /** True when the model can't produce a table yet — i.e. no ROW field.
   *  Measures and column dimensions are optional; without them the
   *  report still lists the groups and their counts. */
  empty: boolean;
}

/** The label for a bucket whose value is BLANK — rows with no driver, no
 *  customer.  Deliberately NOT the em-dash the view paints in empty
 *  cells: that made a real category (the no-driver column) look
 *  identical to "this intersection has nothing in it", so a column
 *  header and a missing number were the same glyph.  A category always
 *  gets words. */
const EMPTY_LABEL = '(none)';
/** Joins a column PATH ("North" + "Q1").  U+0000 can't occur in a bucket
 *  value, so a path id is unambiguous however the data is shaped. */
const PATH_SEP = '\u0000';

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
  // N column dimensions -> N header levels above the value row
  // (Region > Quarter > Sales).  Rows stay single-level in this phase;
  // nesting THOSE needs an expand/collapse tree, not just more headers.
  const off = new Set(model.disabled ?? []);
  const colCols = model.columns
    .filter((k) => !off.has(k))
    .map((k) => cols.get(k))
    .filter((c): c is AnyColumn => !!c);
  const valueFields = model.values.filter((v) => cols.has(v.key) && !off.has(v.key));
  const activeRows = model.rows.filter((k) => !off.has(k));

  const blank: PivotResult = {
    headerLevels: [], leafIds: [], leafValueKeys: [],
    rowFieldLabel: '', bodyRows: [], grandTotal: [],
    grandRowTotal: [], totalLabels: [], hiddenColumns: 0,
    leafWidest: [], totalWidest: [], widestRow: null, empty: true,
  };
  // Only a ROW field is mandatory.  Measures and column dimensions are
  // refinements: switch every measure off and you still get the groups
  // and their counts, which is a real answer ("how do these 500 loads
  // split by customer?") and — more importantly — keeps the report you
  // built ON SCREEN while you toggle a measure to compare with and
  // without.  Blanking to an empty state took the configuration away
  // at exactly the moment the user was experimenting with it.
  if (activeRows.length === 0) return blank;
  // With no measure there is still ONE leaf per column bucket, so the
  // column-group headers keep something to span and the matrix keeps
  // its shape (MUI does the same).  ``leavesPerBucket`` is that width.
  const leavesPerBucket = Math.max(1, valueFields.length);

  // ── Buckets ────────────────────────────────────────────────────────
  // Sorted so the output is deterministic (and so 'YYYY-MM' month
  // buckets fall in chronological order for free).
  // Row dimensions nest: [Company, Customer] gives a Company group with
  // Customer rows beneath it.  Every LEVEL gets its own aggregate row,
  // so a collapsed parent still shows real totals rather than a blank.
  const rowDims = activeRows
    .map((k) => cols.get(k))
    .filter((c): c is AnyColumn => !!c);
  // Guard on the RESOLVED dimensions, not the raw keys: a saved model
  // naming a column the grid no longer has would otherwise pass the
  // check above and render a table with no row identity at all.
  if (rowDims.length === 0) return blank;

  const rowPathIds: string[] = [];        // every prefix, DFS order
  const seenRowPath = new Set<string>();
  const colPaths: string[][] = [];
  const seenPath = new Set<string>();
  const pathOf = (r: Record<string, unknown>) => rowDims.map((c) => bucketOf(r, c));

  // Which column buckets hold at least one real number.  A bucket can
  // have ROWS and still be all dashes (the measure is missing for them),
  // so presence of rows is not the test — presence of a finite measure
  // is.  ``count`` is the exception: it reports the population, so any
  // bucket with rows shows something under it.
  const countsAsValue = valueFields.some((v) => v.aggFn === 'count');
  const colHasValue = new Set<string>();
  for (const r of rows) {
    if (colCols.length) {
      const path = colCols.map((c) => bucketOf(r, c));
      const id = path.join(PATH_SEP);
      if (!seenPath.has(id)) { seenPath.add(id); colPaths.push(path); }
      if (!colHasValue.has(id)) {
        if (countsAsValue) {
          colHasValue.add(id);
        } else {
          for (const v of valueFields) {
            if (Number.isFinite(measureOf(r, cols.get(v.key)!))) {
              colHasValue.add(id);
              break;
            }
          }
        }
      }
    }
    // Register EVERY prefix so parents exist even when a child is the
    // only thing that produced them.
    const rp = pathOf(r);
    for (let d = 1; d <= rp.length; d += 1) {
      const id = rp.slice(0, d).join(PATH_SEP);
      if (!seenRowPath.has(id)) { seenRowPath.add(id); rowPathIds.push(id); }
    }
  }
  // Sorting the full prefix list lexicographically IS depth-first order:
  // "Acme" < "Acme\0Bolt" < "Beta", so a parent always precedes its
  // children and siblings stay together.
  rowPathIds.sort();
  colPaths.sort((a, b) => a.join(PATH_SEP).localeCompare(b.join(PATH_SEP)));
  const keptColPaths = (model.hideEmptyColumns && colCols.length)
    ? colPaths.filter((p) => colHasValue.has(p.join(PATH_SEP)))
    : colPaths;
  const hiddenColumns = colPaths.length - keptColPaths.length;
  // Never prune to nothing: an all-empty report would collapse to the
  // row labels with no explanation, which reads as broken.  Better to
  // show the empty columns and let the count say what happened.
  const usableColPaths = keptColPaths.length ? keptColPaths : colPaths;
  const effectiveColPaths = colCols.length ? usableColPaths : [[]];
  const effectiveColBuckets = effectiveColPaths.map((p) => p.join(PATH_SEP));

  // ── Leaves ─────────────────────────────────────────────────────────
  const leafIds: string[] = [];
  const leafValueKeys: string[] = [];
  for (const cb of effectiveColBuckets) {
    if (valueFields.length === 0) {
      // Empty value key — ``cellsFor`` reads that as "nothing to
      // measure" and emits null, which the view paints as a dash.
      leafIds.push(`${cb}||`);
      leafValueKeys.push('');
      continue;
    }
    for (const v of valueFields) {
      leafIds.push(`${cb}||${v.key}`);
      leafValueKeys.push(v.key);
    }
  }

  // ── Headers ────────────────────────────────────────────────────────
  const valueLevel: PivotHeaderCell[] = [];
  for (const _ of effectiveColPaths) {
    for (const v of valueFields) {
      valueLevel.push({
        label: cols.get(v.key)?.label ?? v.key,
        span: 1,
        aggFn: v.aggFn,
      });
    }
  }
  // One level per column dimension.  A cell spans every leaf beneath it,
  // i.e. (paths sharing this prefix) x (value fields).  Paths are sorted,
  // so equal prefixes are adjacent and a run is a simple scan.
  const dimensionLevels: PivotHeaderCell[][] = colCols.map((dimCol, depth) => {
    const level: PivotHeaderCell[] = [];
    let i = 0;
    while (i < effectiveColPaths.length) {
      const prefix = effectiveColPaths[i].slice(0, depth + 1).join(PATH_SEP);
      let j = i;
      while (
        j < effectiveColPaths.length
        && effectiveColPaths[j].slice(0, depth + 1).join(PATH_SEP) === prefix
      ) j += 1;
      level.push({
        label: labelOf(effectiveColPaths[i][depth], dimCol),
        span: (j - i) * leavesPerBucket,
      });
      i = j;
    }
    return level;
  });
  // A value level naming nothing would be an empty header row.  And
  // with neither columns nor measures there are no levels at all — emit
  // one empty level so the corner cell (the row field's name) still has
  // a row to live in.
  const headerLevels: PivotHeaderCell[][] = valueFields.length
    ? [...dimensionLevels, valueLevel]
    : (dimensionLevels.length ? dimensionLevels : [[]]);

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
  // Per row bucket, ACROSS every column bucket — the Total column.  Kept
  // as a separate collection (not derived from ``collected``) so it
  // reduces through the same engine the cells do.
  const rowTotals = new Map<string, number[]>();   // `${rb}||${valueKey}`
  const allValues = new Map<string, number[]>();   // valueKey — the corner
  const rowCounts = new Map<string, number>();     // rb — the "(4)" badge
  const cellCounts = new Map<string, number>();    // `${rb} ${cb}`
  const colCounts = new Map<string, number>();     // cb
  let totalRowCount = 0;

  for (const r of rows) {
    const rp = pathOf(r);
    const cb = colCols.length
      ? colCols.map((c) => bucketOf(r, c)).join(PATH_SEP)
      : '';
    totalRowCount += 1;
    bump(colCounts, cb);
    // A source row contributes to its own group AND to every ancestor,
    // which is what makes a collapsed parent show a real total.
    for (let d = 1; d <= rp.length; d += 1) {
      const rb = rp.slice(0, d).join(PATH_SEP);
      bump(rowCounts, rb);
      bump(cellCounts, `${rb} ${cb}`);
      for (const v of valueFields) {
        const n = measureOf(r, cols.get(v.key)!);
        if (!Number.isFinite(n)) continue;
        push(collected, `${rb} ${cb}${'||'}${v.key}`, n);
        push(rowTotals, `${rb}||${v.key}`, n);
      }
    }
    for (const v of valueFields) {
      const n = measureOf(r, cols.get(v.key)!);
      if (!Number.isFinite(n)) continue;
      push(totals, `${cb}||${v.key}`, n);
      push(allValues, v.key, n);
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

  const hasChild = new Set(
    rowPathIds
      .map((id) => id.slice(0, id.lastIndexOf(PATH_SEP)))
      .filter((p) => p !== ''),
  );
  const cellsFor = (rb: string) => leafIds.map((leaf, i) => {
    const v = valueFields.find((f) => f.key === leafValueKeys[i]);
    if (!v) return null;                    // no measure selected
    return reduce(
      v.aggFn,
      collected.get(`${rb} ${leaf}`),
      cellCounts.get(`${rb} ${colOf(leaf)}`) ?? 0,
    );
  });

  // Only meaningful with a column dimension: without one, the leaf
  // columns already ARE the per-row totals and a Total column would
  // just repeat them.
  const wantsTotalCol = colCols.length > 0;
  const totalsFor = (rb: string): (number | null)[] => (
    wantsTotalCol
      ? valueFields.map((v) => reduce(
          v.aggFn, rowTotals.get(`${rb}||${v.key}`), rowCounts.get(rb) ?? 0,
        ))
      : []
  );

  const makeRow = (rb: string): PivotBodyRow => {
    const parts = rb.split(PATH_SEP);
    const depth = parts.length - 1;
    return {
      key: rb,
      path: parts,
      depth,
      hasChildren: hasChild.has(rb),
      label: labelOf(parts[depth], rowDims[depth]),
      count: rowCounts.get(rb) ?? 0,
      cells: cellsFor(rb),
      totals: totalsFor(rb),
    };
  };

  // Sorting by a measure has to happen WITHIN each parent — sorting the
  // flat list globally would tear children away from their group.  So
  // group by parent, order each sibling set, then walk depth-first.
  const sortLeafIdx = model.sort ? leafIds.indexOf(model.sort.leaf) : -1;
  // Root sentinel, NOT '': a blank bucket produces the path id '', which
  // would otherwise compute itself as its own parent and recurse forever.
  const ROOT = '\u0001';
  const childrenOf = new Map<string, string[]>();
  for (const id of rowPathIds) {
    const cut = id.lastIndexOf(PATH_SEP);
    const parent = cut < 0 ? ROOT : id.slice(0, cut);
    const arr = childrenOf.get(parent);
    if (arr) arr.push(id); else childrenOf.set(parent, [id]);
  }
  const orderSiblings = (ids: string[]): string[] => {
    if (sortLeafIdx < 0) return [...ids].sort();       // label order
    const dir = model.sort!.dir === 'asc' ? 1 : -1;
    return [...ids].sort((a, b) => {
      const av = cellsFor(a)[sortLeafIdx];
      const bv = cellsFor(b)[sortLeafIdx];
      // Rows with nothing in that column sink to the bottom either way —
      // "no value" is not smaller than every number, it's absent.
      if (av === null && bv === null) return a.localeCompare(b);
      if (av === null) return 1;
      if (bv === null) return -1;
      return av === bv ? a.localeCompare(b) : (av - bv) * dir;
    });
  };
  const bodyRows: PivotBodyRow[] = [];
  const walk = (parent: string) => {
    for (const id of orderSiblings(childrenOf.get(parent) ?? [])) {
      bodyRows.push(makeRow(id));
      walk(id);
    }
  };
  walk(ROOT);

  const grandTotal = leafIds.map((leaf, i) => {
    const v = valueFields.find((f) => f.key === leafValueKeys[i]);
    if (!v) return null;
    return reduce(v.aggFn, totals.get(leaf), colCounts.get(colOf(leaf)) ?? totalRowCount);
  });

  const grandRowTotalCells = wantsTotalCol
    ? valueFields.map((v) => reduce(v.aggFn, allValues.get(v.key), totalRowCount))
    : [];

  // ── Widest candidates ──────────────────────────────────────────────
  // Greatest MAGNITUDE stands in for "longest when formatted": with one
  // formatter per column, more digits means a wider string.  Cheap (one
  // pass) and it only has to be an upper bound good enough to stop the
  // width moving.
  const widerOf = (a: number | null, b: number | null) => {
    if (a === null) return b;
    if (b === null) return a;
    return Math.abs(b) > Math.abs(a) ? b : a;
  };
  const leafWidest = leafIds.map((_, i) => {
    let best = grandTotal[i];
    for (const r of bodyRows) best = widerOf(best, r.cells[i]);
    return best;
  });
  const totalWidest = (wantsTotalCol ? valueFields : []).map((_, i) => {
    let best = grandRowTotalCells[i] ?? null;
    for (const r of bodyRows) best = widerOf(best, r.totals[i] ?? null);
    return best;
  });
  let widestRow: PivotResult['widestRow'] = null;
  let widestScore = -1;
  for (const r of bodyRows) {
    // Indent counts double per level because it is 16px against roughly
    // an 8px character — an estimate, not a measurement.
    const score = r.depth * 2 + r.label.length + String(r.count).length;
    if (score > widestScore) {
      widestScore = score;
      widestRow = { label: r.label, depth: r.depth, count: r.count };
    }
  }

  return {
    headerLevels,
    leafIds,
    leafValueKeys,
    rowFieldLabel: rowDims.map((c) => c.label).join(' / '),
    bodyRows,
    grandTotal,
    grandRowTotal: grandRowTotalCells,
    totalLabels: wantsTotalCol
      ? valueFields.map((v) => cols.get(v.key)?.label ?? v.key)
      : [],
    hiddenColumns: keptColPaths.length ? hiddenColumns : 0,
    leafWidest,
    totalWidest,
    widestRow,
    empty: false,
  };
}

/**
 * Translate an INSERT-BEFORE position into the index to insert at once
 * the item has been removed from the same list.
 *
 * The drag insertion line means "put it before the item currently at
 * ``insertBefore``" — an index into the list AS DISPLAYED, which still
 * contains the item being dragged.  Placing it means removing it first,
 * and that shifts every later position up by one.  So a DOWNWARD move
 * inside one list lands one slot too late unless it is decremented:
 * dragging Customer from [Customer, Date, Company] to before Company
 * (index 2) must insert at 1, or it ends up last and the line has lied.
 *
 * ``fromIndex`` is -1 when the item comes from a different list, where
 * nothing shifts and the position is already correct.
 */
export function insertionIndex(fromIndex: number, insertBefore: number): number {
  return fromIndex >= 0 && fromIndex < insertBefore ? insertBefore - 1 : insertBefore;
}

/** Is this model renderable?  The panel uses it to decide between the
 *  matrix and the "pick a field" hint. */
export function isPivotReady(model: PivotModel): boolean {
  // Counts only what is switched ON — unticking every measure leaves a
  // report with nothing to show, and the empty state has to say so
  // rather than the matrix rendering a header with no numbers under it.
  const off = new Set(model.disabled ?? []);
  return model.rows.some((k) => !off.has(k));
}

/** Drop fields that no longer exist on the grid (a column was removed or
 *  renamed since the model was saved) — the same staleness rule saved
 *  tabs apply to their filters. */
export function prunePivotModel(model: PivotModel, columns: AnyColumn[]): PivotModel {
  const keys = new Set(columns.map((c) => c.key));
  const rows = model.rows.filter((k) => keys.has(k));
  const cols = model.columns.filter((k) => keys.has(k));
  const values = model.values.filter((v) => keys.has(v.key));
  // ``disabled`` only ever describes fields that are ASSIGNED.  Sweeping
  // it here — rather than at each call site that can unassign — makes
  // the invariant hold however the model was written.  Without it, a
  // field switched off and then removed left a ghost entry behind, so
  // re-adding it later brought it back already unticked with nothing on
  // screen explaining why.
  const assigned = new Set([...rows, ...cols, ...values.map((v) => v.key)]);
  return {
    rows,
    columns: cols,
    values,
    // ``sort`` MUST survive the prune.  Rebuilding the model field-by-
    // field silently dropped it, which killed the whole feature: every
    // header click wrote a sort that the next render threw away, so the
    // rows never reordered and the caret never appeared.  A sort naming
    // a leaf that no longer exists is handled downstream (pivot() falls
    // back to label order) — that is a rendering decision, not a reason
    // to discard the user's choice here.
    sort: model.sort ?? null,
    disabled: (model.disabled ?? []).filter((k) => assigned.has(k)),
    hideEmptyColumns: model.hideEmptyColumns ?? false,
    // Default OFF.  Note what this means for an existing saved report:
    // it has no explicit value, so it picks up the new default and its
    // edges unfreeze on next load.  That is the intended change, not a
    // migration bug — but it IS a visible change to saved reports, which
    // is why the flag is written explicitly from here on, so a future
    // flip of the default can't silently move them again.
    pinRowLabels: model.pinRowLabels ?? false,
    pinTotals: model.pinTotals ?? false,
  };
}


/**
 * The pivot matrix as CSV rows (a 2-D string grid; the caller joins it).
 *
 * The export must produce WHAT IS ON SCREEN.  In pivot mode the flat
 * record list isn't on screen — exporting it would hand the operator a
 * different artifact from the one they configured and are looking at.
 *
 * Nested headers flatten into one readable name per leaf, outermost
 * first: "North / Q1 / Sales (sum)".  Spreadsheets have no notion of our
 * spanning header cells, so a single unambiguous name per column beats
 * trying to reproduce the visual nesting with blank cells.
 *
 * Numbers are emitted RAW (unformatted) on purpose: a CSV is opened in a
 * spreadsheet to be computed with, and "$1,234.50" is a string there,
 * not money.  An empty intersection stays EMPTY rather than 0 — the same
 * distinction the matrix draws with its dash.
 */
export function pivotToCsvRows(result: PivotResult): string[][] {
  if (result.empty) return [];

  // Build one flattened label per leaf by walking the dimension levels
  // and repeating each cell across the leaves it spans.
  const perLevelLabels: string[][] = result.headerLevels.map((level) => {
    const out: string[] = [];
    for (const cell of level) {
      for (let i = 0; i < cell.span; i += 1) out.push(cell.label);
    }
    return out;
  });
  const leafHeaders = result.leafIds.map((_, i) => {
    const parts = perLevelLabels.map((labels) => labels[i]).filter(Boolean);
    const last = result.headerLevels[result.headerLevels.length - 1][i];
    // The measure's agg fn is part of the column's identity — two
    // "Rate" columns differing only by sum/avg must not collide.
    if (last?.aggFn) parts[parts.length - 1] = `${parts[parts.length - 1]} (${last.aggFn})`;
    return parts.join(' / ');
  });

  const header = [result.rowFieldLabel, 'Rows', ...leafHeaders];
  const body = result.bodyRows.map((row) => [
    row.label,
    String(row.count),
    ...row.cells.map((v) => (v === null ? '' : String(v))),
  ]);
  const total = [
    'Total',
    String(result.bodyRows.reduce((n, r) => n + r.count, 0)),
    ...result.grandTotal.map((v) => (v === null ? '' : String(v))),
  ];
  return [header, ...body, total];
}


/**
 * The source rows behind one cell — "which loads made up that $400?".
 *
 * Recomputed on demand rather than cached per cell: a matrix of R x C
 * cells would otherwise hold R x C row arrays alive for a question the
 * operator asks about ONE of them.
 *
 * ``rowPath`` may be a PREFIX (a collapsed parent), in which case every
 * descendant's rows are returned — the parent's number is their sum, so
 * its drill-down has to be their union.  ``colPath`` empty means "every
 * column" (the row-total case, and grids with no column dimension).
 */
export function pivotCellRows(
  rows: Record<string, unknown>[],
  model: PivotModel,
  columns: AnyColumn[],
  rowPath: string[],
  colPath: string[],
): Record<string, unknown>[] {
  const cols = new Map(columns.map((c) => [c.key, c]));
  // Must mirror pivot()'s own filtering EXACTLY — a drill-down that
  // matched on a switched-off dimension would return rows the number on
  // screen never counted.
  const off = new Set(model.disabled ?? []);
  const dimsOf = (keys: string[]) => keys
    .filter((k) => !off.has(k))
    .map((k) => cols.get(k))
    .filter((c): c is AnyColumn => !!c);
  const rowDims = dimsOf(model.rows);
  const colDims = dimsOf(model.columns);
  return rows.filter((r) => {
    for (let i = 0; i < rowPath.length && i < rowDims.length; i += 1) {
      if (bucketOf(r, rowDims[i]) !== rowPath[i]) return false;
    }
    for (let i = 0; i < colPath.length && i < colDims.length; i += 1) {
      if (bucketOf(r, colDims[i]) !== colPath[i]) return false;
    }
    return true;
  });
}

/** Split a leaf id back into its column path + value key. */
export function splitLeafId(leaf: string): { colPath: string[]; valueKey: string } {
  const cut = leaf.lastIndexOf('||');
  const cb = leaf.slice(0, cut);
  return {
    colPath: cb === '' ? [] : cb.split(PATH_SEP),
    valueKey: leaf.slice(cut + 2),
  };
}
