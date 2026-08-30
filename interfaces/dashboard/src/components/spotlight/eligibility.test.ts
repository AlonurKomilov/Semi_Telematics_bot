/**
 * The eligibility verdicts — pure logic, no DOM.
 *
 * done and skipped are FINAL: both are the user answering, and asking
 * an answered question teaches people to stop reading the intro.
 * snoozed is not an answer — closing a window says "not now", so it
 * re-offers, but only after SNOOZE_DAYS.
 */
import { describe, expect, it } from 'vitest';
import { isEligible, SNOOZE_DAYS, type TourSpec } from './types';

const tour: TourSpec = {
  key: 'maintenance.bulk_add',
  feature: 'maintenance',
  steps: [{ anchor: 'x', advanceOn: 'click' }],
  relevant: (ctx) => ctx.canCreate && ctx.count >= 5,
};
const ctx = { count: 9, canCreate: true };
const daysAgo = (n: number) =>
  new Date(Date.now() - n * 86_400_000).toISOString();

describe('isEligible', () => {
  it('offers a relevant tour with no verdict', () => {
    expect(isEligible(tour, ctx, {})).toBe(true);
  });
  it('never offers below the relevance line', () => {
    expect(isEligible(tour, { count: 2, canCreate: true }, {})).toBe(false);
    expect(isEligible(tour, { count: 9, canCreate: false }, {})).toBe(false);
  });
  it('done and skipped are final', () => {
    for (const s of ['done', 'skipped'] as const) {
      expect(isEligible(tour, ctx,
        { [tour.key]: { s, t: daysAgo(400) } })).toBe(false);
    }
  });
  it('snoozed re-offers only after the snooze window', () => {
    expect(isEligible(tour, ctx,
      { [tour.key]: { s: 'snoozed', t: daysAgo(SNOOZE_DAYS - 1) } })).toBe(false);
    expect(isEligible(tour, ctx,
      { [tour.key]: { s: 'snoozed', t: daysAgo(SNOOZE_DAYS + 1) } })).toBe(true);
  });
  it('a verdict on ONE tour never mutes another', () => {
    const other = { ...tour, key: 'maintenance.other' };
    expect(isEligible(other, ctx,
      { [tour.key]: { s: 'skipped', t: daysAgo(1) } })).toBe(true);
  });
});
