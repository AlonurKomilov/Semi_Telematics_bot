/**
 * The plain-text value of one grid cell.
 *
 * Two features need to answer the same question — "what does this cell
 * SAY?" — and they must agree, because an operator who can read a value
 * on screen expects both to find it: CSV export writes this text, and
 * the grid's global search matches against it.  When they disagreed,
 * search silently skipped every column that renders through an
 * accessor while export handled it fine.
 */

import type { AnyColumn } from '../types';

/** Resolve a column's plain text for a given row.  Preference order:
 *  ``csvValue`` (explicit opt-in) → ``filterLabel`` (display label for
 *  badge columns: "Critical", not the colour code) → ``filterValue``
 *  (match value) → the raw cell value at ``key``.  A column that
 *  already opted into filtering therefore gets readable export AND
 *  searchable text for free. */
export function cellText(col: AnyColumn, row: Record<string, unknown>): string {
  if (col.csvValue) return col.csvValue(row);
  if (col.filterLabel) return col.filterLabel(row);
  if (col.filterValue) return col.filterValue(row);

  const v = row[col.key];
  if (v == null) return '';
  // Dates as ISO so downstream tools (Excel, Sheets) parse them.
  // Anything more elaborate (locale formats) goes through ``csvValue``.
  if (v instanceof Date) return v.toISOString();
  if (Array.isArray(v)) {
    // ``String([a, b])`` already comma-joins primitives, which is what
    // both callers want.  Bail only when an element is itself an
    // object, where the join would splice "[object Object]" in.
    return v.some(x => x !== null && typeof x === 'object') ? '' : String(v);
  }
  // A bare object stringifies to "[object Object]" — meaningless in a
  // CSV, and worse in search, where the needle "object" would match
  // every row that has one.  Only an accessor can say what it means.
  if (typeof v === 'object') return '';
  return String(v);
}
