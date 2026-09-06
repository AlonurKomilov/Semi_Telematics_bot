import { describe, it, expect } from 'vitest';
import { ageMs, describeAge, formatAge, stalenessOf, STALE_MS, VERY_STALE_MS } from './freshness';

const NOW = Date.parse('2026-09-04T12:00:00Z');
const ago = (ms: number) => new Date(NOW - ms).toISOString();

describe('position freshness', () => {
  it('reads an age off a timestamp', () => {
    expect(ageMs(ago(5_000), NOW)).toBe(5_000);
    expect(ageMs(null, NOW)).toBeNull();
    expect(ageMs('not a date', NOW)).toBeNull();
    expect(ageMs('', NOW)).toBeNull();
  });
  it('a clock a little ahead of ours is now, not the future', () => {
    expect(ageMs(new Date(NOW + 4_000).toISOString(), NOW)).toBe(0);
  });
  it('escalates at the dashboard thresholds, not its own', () => {
    expect(stalenessOf(ageMs(ago(STALE_MS - 1), NOW))).toBe('fresh');
    expect(stalenessOf(ageMs(ago(STALE_MS), NOW))).toBe('stale');
    expect(stalenessOf(ageMs(ago(VERY_STALE_MS), NOW))).toBe('very_stale');
    expect(stalenessOf(null)).toBe('unknown');
  });
  it('compacts to whole units', () => {
    expect(formatAge(4_000)).toBe('4s');
    expect(formatAge(4 * 60_000)).toBe('4m');
    expect(formatAge(3 * 3600_000)).toBe('3h');
    expect(formatAge(17 * 24 * 3600_000)).toBe('17d');
    expect(formatAge(null)).toBe('');
  });
  it('says so out loud when there is no time at all', () => {
    expect(describeAge(null)).toBe('No position time reported');
    expect(describeAge(17 * 24 * 3600_000)).toBe('Position updated 17d ago');
  });
});
