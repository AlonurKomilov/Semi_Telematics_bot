/**
 * "None in this view" is true and incomplete.
 *
 * The owner switched Fuel stations on over Chicago and read it, and the
 * sentence gave them nothing to do with it: there genuinely might be no
 * fuel stop in view, or their own brand chip might be hiding one, or —
 * the case nothing on screen could have told them — the OpenStreetMap
 * extract behind the layer might be months behind, so a truck stop that
 * opened since simply is not in it.  Measured 2026-09-13, the two
 * mirrors this server can reach were stamped 2026-06-01 and 2026-07-28.
 *
 * This is the half that decides WHEN the age is worth saying.  Said
 * always, it is noise on every quiet row; said never, the owner is left
 * to conclude the product is wrong.
 */
import { describe, expect, it } from 'vitest';

import { SOURCE_STALE_DAYS, staleSourceAge } from './layers';

const DAY = 86_400_000;
const NOW = Date.UTC(2026, 8, 13, 12, 0, 0);
const daysAgo = (n: number) => new Date(NOW - n * DAY).toISOString();

describe('when the source’s age is worth saying', () => {
  it('says nothing when there is no date to say', () => {
    expect(staleSourceAge(null, NOW)).toBeNull();
    expect(staleSourceAge(undefined, NOW)).toBeNull();
    expect(staleSourceAge('', NOW)).toBeNull();
  });

  it('says nothing about a date it cannot read', () => {
    // A mirror sending something unexpected must not print "NaNd old".
    expect(staleSourceAge('not a date', NOW)).toBeNull();
  });

  it('stays quiet while the lag is ordinary', () => {
    // OSM itself takes days to carry a new place; below the threshold,
    // "the data is behind" is not yet the better explanation.
    expect(staleSourceAge(daysAgo(0), NOW)).toBeNull();
    expect(staleSourceAge(daysAgo(SOURCE_STALE_DAYS - 1), NOW)).toBeNull();
  });

  it('speaks from the threshold onward, in whole days', () => {
    expect(staleSourceAge(daysAgo(SOURCE_STALE_DAYS), NOW)).toBe(`${SOURCE_STALE_DAYS}d`);
    expect(staleSourceAge(daysAgo(104), NOW)).toBe('104d');
  });

  it('never reports a negative age when a clock is skewed forward', () => {
    // A mirror a few seconds ahead of us is not "-1d old"; it is now.
    expect(staleSourceAge(new Date(NOW + 5_000).toISOString(), NOW)).toBeNull();
  });

  it('null means SAY NOTHING, never an age of zero', () => {
    // The render is `staleAge && ...`, so a helper returning '0d' below
    // the threshold would print "OSM data 0d old" on every healthy row.
    expect(staleSourceAge(daysAgo(1), NOW)).not.toBe('0d');
  });
});
