// ── DataGrid aggregation — pure engine ──────────────────────────────
//
// The framework-free core of the aggregation feature: the reducing math,
// value normalizers, and the "which functions does this column offer"
// rule.  Kept out of DataGrid.tsx so it can be unit-tested in isolation
// (the two nastiest bugs this feature has had — a null date folding in as
// epoch 1970, and a null number folding in as 0 — both live here and are
// covered by aggregation.test.ts).  The React-coupled parts (renderAggCell,
// the footer memo, the <tfoot>) stay in the component.

import type { AggFn, AnyColumn } from '../../types';
import { AGG_FN_ORDER } from '../../types';

/**
 * Reduce a column's numeric values to a single aggregate.  `count`
 * ignores the values and returns the row count (MUI's `size`); the rest
 * fold over the finite numbers.  min/max use `reduce` (not
 * `Math.min(...values)`) so a large filtered set can't blow the call
 * stack via argument spread.  Returns null for an empty value set (→ the
 * footer renders "—").
 */
export function computeAggregate(fn: AggFn, values: number[], rowCount: number): number | null {
  if (fn === 'count') return rowCount;
  if (values.length === 0) return null;
  switch (fn) {
    case 'sum': return values.reduce((a, b) => a + b, 0);
    case 'avg': return values.reduce((a, b) => a + b, 0) / values.length;
    case 'min': return values.reduce((a, b) => Math.min(a, b), Infinity);
    case 'max': return values.reduce((a, b) => Math.max(a, b), -Infinity);
    default: return null;
  }
}

/**
 * Fallback footer formatting when a column declares no `aggFormat` — a
 * plain locale number, with decimals only for a (non-integer) average.
 */
export function formatAggDefault(value: number, fn: AggFn): string {
  return value.toLocaleString(undefined, {
    maximumFractionDigits: fn === 'avg' ? 2 : 0,
  });
}

/**
 * Normalize a number column's raw value.  Null / undefined / '' → NaN so
 * a MISSING value is DROPPED by the caller's `Number.isFinite` filter —
 * NOT folded in as 0 (`Number(null) === 0`, `Number('') === 0`), which
 * would drag an average down or masquerade as the minimum.  A real 0
 * still counts.
 */
export function toAggNumber(v: unknown): number {
  if (v == null || v === '') return NaN;
  return Number(v);
}

/**
 * Normalize a date column's raw value (Date / ISO string / ms number) to
 * a comparable ms timestamp.  Null / undefined / '' → NaN so a missing
 * date is dropped — NOT folded in as epoch 0 (`new Date(null)` /
 * `new Date(0)` both yield 1970-01-01, which would masquerade as the
 * "earliest" min).
 */
export function toAggTimestamp(v: number | string | Date | null | undefined): number {
  if (v == null || v === '') return NaN;
  if (v instanceof Date) return v.getTime();
  if (typeof v === 'number') return v;
  return new Date(v).getTime();
}

/**
 * Date columns only offer earliest / latest — summing or averaging dates
 * is meaningless, and `count` on a date is ambiguous (it's the whole-view
 * ROW count, NOT "how many rows have a date", so on a mostly-null column
 * like `reviewed_at` it misleads).  Number columns offer all five.
 */
export const DATE_AGG_FNS: readonly AggFn[] = ['min', 'max'];

/** The functions the column's ⋮ menu offers: its own `aggFns` if set,
 *  else the default for its `aggType` (date → min/max, number → all five). */
export function offeredAggFns(col?: AnyColumn): readonly AggFn[] {
  if (!col) return AGG_FN_ORDER;
  if (col.aggFns) return col.aggFns;
  return col.aggType === 'date' ? DATE_AGG_FNS : AGG_FN_ORDER;
}
