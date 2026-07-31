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
 *  Carries only what the drill needs: the coordinates to QUERY by and
 *  the words to SHOW.  Not the whole `PivotBodyRow` — depth, children,
 *  cells and totals all describe how a row is drawn, which the dialog
 *  has no business holding on to.
 *
 *  ⚠️ The query keys and the display strings are SEPARATE fields, and
 *  that is not redundancy.  A bucket's key is `2026-01` while its header
 *  reads `Jan 2026`; joining the key path for the title would print the
 *  raw buckets back at the reader. */
export interface DrillTarget {
  /** Bucket path of the row, outermost first.  May be a PREFIX (a
   *  collapsed parent), and EMPTY means every row — the grand total. */
  rowPath: string[];
  /** Display label per level of `rowPath`, outermost first.
   *
   *  The whole ancestor chain, not just the leaf: "Bolt" alone is
   *  ambiguous the moment two companies both have a customer by that
   *  name, and a pivot's whole point is that the same bucket name
   *  recurs under different parents. */
  rowLabels: string[];
  /** Column bucket path.  EMPTY means every column — which is exactly
   *  what the Total column aggregates over. */
  colPath: string[];
  /** What to call the column coordinate: the bucket path for a leaf
   *  cell, "Total" for the Total column, empty for a report with no
   *  column dimension (where there is no column coordinate to name). */
  colLabel: string;
  /** Which measure the figure came from. */
  valueKey: string;
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
