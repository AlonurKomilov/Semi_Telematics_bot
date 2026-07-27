// ── Derived pivot dimensions ─────────────────────────────────────────
//
// "Rate by customer per MONTH" is the archetypal pivot question, but a
// raw timestamp is a useless dimension: bucketing by exact instant gives
// one column per row.  Every date column therefore needs a bucketing
// accessor — and writing one per column by hand (as Loads originally did)
// doesn't scale past the first grid.
//
// This generates them: any date column offers Year / Quarter / Month
// automatically, computed in the ACCOUNT timezone.  The raw date column
// itself is DROPPED from the dimension list, because "group by this exact
// timestamp" is never the question.
//
// The derived entries are synthetic — they exist only in the pivot
// pickers, never in the grid's own column set — so no page has to declare
// anything to gain them.

import type { AnyColumn } from '../../../types';
import {
  yearInTimeZone, quarterInTimeZone, monthInTimeZone, formatMonth,
} from '../../../utils/datetime';

/** Separator between the source column key and the derived grain.  Not a
 *  dot: `defFor` in the preferences registry treats `table.<id>.<part>`
 *  as a family key, and a dotted derived key inside a stored pivot model
 *  would be needlessly confusing to read. */
export const DERIVED_SEP = '::';

export type DateGrain = 'year' | 'quarter' | 'month';

/** Is this column a date? Reuses the signals the grid already carries —
 *  a page that declared `aggType: 'date'` or a date-range filter has
 *  already told us. */
export function isDateColumn(col: AnyColumn): boolean {
  return col.aggType === 'date' || col.filterMode === 'date-range';
}

const GRAINS: { grain: DateGrain; suffix: string }[] = [
  { grain: 'year', suffix: 'Year' },
  { grain: 'quarter', suffix: 'Quarter' },
  { grain: 'month', suffix: 'Month' },
];

function bucketFor(grain: DateGrain, raw: unknown, tz: string): string {
  const v = String(raw ?? '');
  if (!v) return '';
  if (grain === 'year') return yearInTimeZone(v, tz);
  if (grain === 'quarter') return quarterInTimeZone(v, tz);
  return monthInTimeZone(v, tz);
}

/**
 * The dimension list the pivot pickers should offer.
 *
 * Non-date `pivotable` columns pass through. A date column with its own
 * `pivotValue` also passes through untouched — the page author chose a
 * bucketing deliberately and that wins. Otherwise the date column is
 * REPLACED by its Year / Quarter / Month grains.
 */
export function derivePivotDimensions(columns: AnyColumn[], tz: string): AnyColumn[] {
  const out: AnyColumn[] = [];
  for (const col of columns) {
    if (!col.pivotable) { out.push(col); continue; }
    if (!isDateColumn(col) || col.pivotValue) { out.push(col); continue; }
    for (const { grain, suffix } of GRAINS) {
      out.push({
        ...col,
        key: `${col.key}${DERIVED_SEP}${grain}`,
        label: `${col.label} (${suffix})`,
        // Derived dimensions are pivot-only: they must never be sorted,
        // filtered or aggregated as if they were real columns.
        sortable: false,
        filterable: false,
        aggregable: false,
        pivotable: true,
        pivotValue: (row) => bucketFor(grain, (row as Record<string, unknown>)[col.key], tz),
        pivotLabel: grain === 'month' ? formatMonth : undefined,
      });
    }
  }
  return out;
}
