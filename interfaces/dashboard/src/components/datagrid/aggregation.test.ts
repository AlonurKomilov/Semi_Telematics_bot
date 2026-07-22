import { describe, it, expect } from 'vitest';
import type { AnyColumn } from '../../types';
import {
  computeAggregate, formatAggDefault, toAggNumber, toAggTimestamp, offeredAggFns,
} from './aggregation';

describe('computeAggregate', () => {
  it('reduces the five functions over a value set', () => {
    const v = [10, 20, 30, 40];
    expect(computeAggregate('sum', v, v.length)).toBe(100);
    expect(computeAggregate('avg', v, v.length)).toBe(25);
    expect(computeAggregate('min', v, v.length)).toBe(10);
    expect(computeAggregate('max', v, v.length)).toBe(40);
  });

  it('count returns the ROW count and ignores the values', () => {
    expect(computeAggregate('count', [], 42)).toBe(42);
    expect(computeAggregate('count', [1, 2, 3], 42)).toBe(42);
  });

  it('returns null for an empty value set (→ footer shows "—"), never Infinity', () => {
    expect(computeAggregate('sum', [], 0)).toBeNull();
    expect(computeAggregate('avg', [], 0)).toBeNull();
    // min/max must NOT leak their ±Infinity seed when there are no values.
    expect(computeAggregate('min', [], 5)).toBeNull();
    expect(computeAggregate('max', [], 5)).toBeNull();
  });

  it('avg is not rounded', () => {
    expect(computeAggregate('avg', [1, 2], 2)).toBe(1.5);
    expect(computeAggregate('avg', [1, 2, 2], 3)).toBeCloseTo(1.6667, 3);
  });

  it('min/max use reduce, not argument spread — a huge set does not blow the stack', () => {
    // Math.min(...big) would throw RangeError around ~100k args; reduce is safe.
    const big = Array.from({ length: 200_000 }, (_, i) => i);
    expect(computeAggregate('max', big, big.length)).toBe(199_999);
    expect(computeAggregate('min', big, big.length)).toBe(0);
  });

  it('handles negatives (e.g. penalty points ≤ 0)', () => {
    expect(computeAggregate('sum', [-4, -8], 2)).toBe(-12);
    expect(computeAggregate('min', [-4, -8, -2], 3)).toBe(-8);
  });
});

describe('toAggNumber (number-column normalizer)', () => {
  it('EXCLUDES missing values — null/undefined/"" → NaN, never 0 (regression)', () => {
    // Number(null) === 0 and Number('') === 0 — the bug this guards.
    expect(toAggNumber(null)).toBeNaN();
    expect(toAggNumber(undefined)).toBeNaN();
    expect(toAggNumber('')).toBeNaN();
  });

  it('a REAL 0 still counts (must not be excluded)', () => {
    expect(toAggNumber(0)).toBe(0);
    expect(toAggNumber('0')).toBe(0);
  });

  it('coerces numbers and numeric strings; garbage → NaN', () => {
    expect(toAggNumber(5)).toBe(5);
    expect(toAggNumber('5.5')).toBe(5.5);
    expect(toAggNumber('abc')).toBeNaN();
  });

  it('regression: a null in the set does NOT drag the average down', () => {
    const raw = [null, 10, 20];
    const nums = raw.map(toAggNumber).filter((n) => Number.isFinite(n));
    expect(nums).toEqual([10, 20]);
    expect(computeAggregate('avg', nums, raw.length)).toBe(15); // not (0+10+20)/3 = 10
  });

  it('but a genuine 0 IS included in the average', () => {
    const raw = [0, 10, 20];
    const nums = raw.map(toAggNumber).filter((n) => Number.isFinite(n));
    expect(computeAggregate('avg', nums, raw.length)).toBe(10); // (0+10+20)/3
  });
});

describe('toAggTimestamp (date-column normalizer)', () => {
  it('EXCLUDES missing dates — null/undefined/"" → NaN, never epoch 0 (regression)', () => {
    // new Date(null)/new Date(0) both → 1970-01-01; must be NaN so it drops.
    expect(toAggTimestamp(null)).toBeNaN();
    expect(toAggTimestamp(undefined)).toBeNaN();
    expect(toAggTimestamp('')).toBeNaN();
  });

  it('parses Date / ms number / ISO string / YYYY-MM-DD', () => {
    const d = new Date('2026-07-20T12:00:00Z');
    expect(toAggTimestamp(d)).toBe(d.getTime());
    expect(toAggTimestamp(1_700_000_000_000)).toBe(1_700_000_000_000);
    expect(toAggTimestamp('2026-07-20')).toBe(new Date('2026-07-20').getTime());
    expect(toAggTimestamp('2026-07-20T20:09:00Z')).toBe(Date.parse('2026-07-20T20:09:00Z'));
  });

  it('unparseable string → NaN', () => {
    expect(toAggTimestamp('not-a-date')).toBeNaN();
  });

  it('regression: a null date does NOT become the "earliest" (1970) min', () => {
    const raw = [null, '2026-07-20', '2026-07-25'];
    const ts = raw.map(toAggTimestamp).filter((n) => Number.isFinite(n));
    expect(ts).toHaveLength(2);
    const min = computeAggregate('min', ts, raw.length);
    expect(min).toBe(new Date('2026-07-20').getTime());
    expect(min).not.toBe(0); // the old bug returned epoch 0
  });
});

describe('formatAggDefault', () => {
  it('caps avg at 2 decimals; other functions show whole numbers', () => {
    expect(formatAggDefault(1234.5678, 'avg')).toBe('1,234.57');
    expect(formatAggDefault(1234.5678, 'sum')).toBe('1,235');
    expect(formatAggDefault(1000, 'max')).toBe('1,000');
    expect(formatAggDefault(42, 'count')).toBe('42');
  });
});

describe('offeredAggFns', () => {
  const col = (over: Partial<AnyColumn>): AnyColumn =>
    ({ key: 'k', label: 'K', ...over } as AnyColumn);

  it('undefined column → all five', () => {
    expect(offeredAggFns(undefined)).toEqual(['sum', 'avg', 'min', 'max', 'count']);
  });

  it('a number column (no aggType) → all five', () => {
    expect(offeredAggFns(col({ aggregable: true }))).toEqual(['sum', 'avg', 'min', 'max', 'count']);
  });

  it('a date column → min/max only (no sum/avg/count)', () => {
    expect(offeredAggFns(col({ aggregable: true, aggType: 'date' }))).toEqual(['min', 'max']);
  });

  it("an explicit aggFns wins over the aggType default", () => {
    expect(offeredAggFns(col({ aggregable: true, aggFns: ['sum'] }))).toEqual(['sum']);
    expect(offeredAggFns(col({ aggregable: true, aggType: 'date', aggFns: ['max'] }))).toEqual(['max']);
  });
});
