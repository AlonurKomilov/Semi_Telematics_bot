// ── Drill-down: the source rows behind one cell ──────────────────────
//
// A pivot cell is an ANSWER ("$1,700"); the drill is the question that
// follows it ("which loads?").  That is one feature, so it gets one home:
// this file is the pure half (what rows are behind a cell), DrillDialog
// is the React half (how they're shown).  Nothing outside this folder
// imports either.
//
// ``splitLeafId`` deliberately stays in ``pivot.ts``, next to the code
// that BUILDS leaf ids — a decoder living apart from its encoder is how
// a format change breaks something silently.  Drill calls it; it doesn't
// own it.

import type { AnyColumn } from '../../../types';
import { bucketOf, type PivotModel } from './pivot';

/** Which cell the operator opened.
 *
 *  Carries the row's PATH and LABEL rather than the whole `PivotBodyRow`:
 *  everything else on that object (depth, children, cells, totals)
 *  describes how the row is drawn, which the drill has no business
 *  holding on to. */
export interface DrillTarget {
  /** Bucket path of the row, outermost first.  May be a PREFIX. */
  rowPath: string[];
  /** What to call it in the dialog's title. */
  rowLabel: string;
  /** Index into `PivotResult.leafIds` — identifies the column + measure. */
  leafIdx: number;
}

/**
 * The source rows behind one cell.
 *
 * Recomputed on demand rather than cached per cell: a matrix of R x C
 * cells would otherwise hold R x C row arrays alive for a question the
 * operator asks about ONE of them.
 *
 * ``rowPath`` may be a PREFIX (a collapsed parent), in which case every
 * descendant's rows are returned — the parent's number is their sum, so
 * its drill-down has to be their union.  ``colPath`` empty means "every
 * column" (the row-total case, and grids with no column dimension).
 *
 * ⚠️ This must mirror ``pivot()``'s own filtering EXACTLY, and the two
 * now live in different files — so the mirror is held by a test
 * (`drill.test.ts`, "ignores a switched-off dimension") rather than by
 * being adjacent.  Change how `pivot()` selects rows and this changes
 * with it, or the dialog hands back rows the number on screen never
 * counted.
 */
export function pivotCellRows(
  rows: Record<string, unknown>[],
  model: PivotModel,
  columns: AnyColumn[],
  rowPath: string[],
  colPath: string[],
): Record<string, unknown>[] {
  const cols = new Map(columns.map((c) => [c.key, c]));
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
