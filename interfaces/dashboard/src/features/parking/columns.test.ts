/**
 * Pure helpers behind the Parking grid's Duration column.
 *
 * ``formatDuration`` earns a test because it already shipped a bug: it
 * rounded the HOUR REMAINDER, which can reach 24, so 71.9h rendered as
 * "2d 24h" instead of "3d 0h".  That is not an edge case in this data —
 * long stops cluster just under a day boundary, so it was visible on
 * nearly every row of a vehicle's history.  The boundary cases below are
 * the ones the old implementation got wrong.
 */
import { describe, it, expect } from 'vitest';
import { formatDuration, durationToneClass, mapsUrl } from './columns';

describe('formatDuration', () => {
  it('renders sub-hour stops in minutes', () => {
    expect(formatDuration(0)).toBe('0m');
    expect(formatDuration(0.25)).toBe('15m');
    expect(formatDuration(0.99)).toBe('59m');
  });

  it('renders same-day stops in decimal hours', () => {
    expect(formatDuration(1)).toBe('1.0h');
    expect(formatDuration(3.24)).toBe('3.2h');
    expect(formatDuration(23.9)).toBe('23.9h');
  });

  it('renders multi-day stops as days + hours', () => {
    expect(formatDuration(24)).toBe('1d 0h');
    expect(formatDuration(41)).toBe('1d 17h');
    expect(formatDuration(50)).toBe('2d 2h');
  });

  // THE REGRESSION.  Rounding the remainder let it hit 24.
  it('never renders 24 in the hours slot', () => {
    for (const h of [23.6, 47.6, 71.9, 95.7, 119.5]) {
      const out = formatDuration(h);
      expect(out, `${h}h rendered as ${out}`).not.toMatch(/\b24h$/);
    }
  });

  it('rolls a near-boundary stop up to the next day', () => {
    expect(formatDuration(71.9)).toBe('3d 0h');
    expect(formatDuration(47.6)).toBe('2d 0h');
  });
});

describe('durationToneClass', () => {
  it('escalates neutral → warn → danger at 2h and 8h', () => {
    expect(durationToneClass(1.9)).toBe('');
    expect(durationToneClass(2)).toBe('text-warn');
    expect(durationToneClass(7.9)).toBe('text-warn');
    expect(durationToneClass(8)).toBe('text-danger font-medium');
  });
});

describe('mapsUrl', () => {
  it('builds a coordinate query', () => {
    expect(mapsUrl(40.7, -74.01)).toBe(
      'https://www.google.com/maps?q=40.7,-74.01',
    );
  });
});
