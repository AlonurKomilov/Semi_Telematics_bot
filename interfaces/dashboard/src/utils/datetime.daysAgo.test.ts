/**
 * ``daysAgoInTimeZone`` — the day-count → date-bound conversion behind
 * every "last N days" window that talks to an API wanting a date.
 *
 * The DST cases are the reason this lives in the datetime SSOT rather
 * than inline on a page: a local-time subtraction crosses a 23-hour day
 * each spring and a 25-hour day each autumn, and lands a date early or
 * late exactly twice a year — the kind of bug that is invisible in
 * testing and reported as "the report missed a day".
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { daysAgoInTimeZone } from './datetime';

function at(iso: string) {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(iso));
}
afterEach(() => vi.useRealTimers());

describe('daysAgoInTimeZone', () => {
  it('counts back whole calendar days', () => {
    at('2026-08-10T15:00:00Z');
    expect(daysAgoInTimeZone(7, 'UTC')).toBe('2026-08-03');
    expect(daysAgoInTimeZone(1, 'UTC')).toBe('2026-08-09');
  });

  it('day 0 is today, not yesterday', () => {
    at('2026-08-10T15:00:00Z');
    expect(daysAgoInTimeZone(0, 'UTC')).toBe('2026-08-10');
  });

  it('crosses month and year boundaries', () => {
    at('2026-03-02T12:00:00Z');
    expect(daysAgoInTimeZone(7, 'UTC')).toBe('2026-02-23');
    at('2026-01-03T12:00:00Z');
    expect(daysAgoInTimeZone(7, 'UTC')).toBe('2025-12-27');
  });

  // Leap day exists in 2028; counting back across it must not skip it.
  it('counts the leap day', () => {
    at('2028-03-01T12:00:00Z');
    expect(daysAgoInTimeZone(1, 'UTC')).toBe('2028-02-29');
  });

  // The account's zone decides which DAY it is, so a US-evening timestamp
  // that is already tomorrow in UTC must still count back from today.
  it('anchors on the account timezone, not UTC', () => {
    at('2026-08-10T04:00:00Z');           // 2026-08-09 21:00 in Denver
    expect(daysAgoInTimeZone(7, 'America/Denver')).toBe('2026-08-02');
    expect(daysAgoInTimeZone(7, 'UTC')).toBe('2026-08-03');
  });

  // Spring forward (2026-03-08, US) and fall back (2026-11-01): a window
  // spanning either must still be exactly N days wide.
  it('is exact across both DST transitions', () => {
    at('2026-03-10T18:00:00Z');
    expect(daysAgoInTimeZone(7, 'America/Denver')).toBe('2026-03-03');
    at('2026-11-03T18:00:00Z');
    expect(daysAgoInTimeZone(7, 'America/Denver')).toBe('2026-10-27');
  });
});
