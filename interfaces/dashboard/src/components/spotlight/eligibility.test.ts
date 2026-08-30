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

describe('signals — offered to the right person, retired for the wrong one', () => {
  const sig = (solo: number, grouped: number) => ({
    'maintenance_task:create': { total: solo + grouped, solo, grouped },
  });
  const smart: TourSpec = {
    ...tour,
    relevant: (ctx) => {
      const s = ctx.signals?.['maintenance_task:create'];
      return s ? s.solo >= 5 : ctx.count >= 5;
    },
    adopted: (ctx) =>
      (ctx.signals?.['maintenance_task:create']?.grouped ?? 0) > 0,
  };

  it('offers on one-at-a-time evidence', () => {
    expect(isEligible(smart, { ...ctx, signals: sig(6, 0) }, {})).toBe(true);
  });
  it('retires unseen for someone who already uses the bulk path', () => {
    // Six solo creates AND a bulk group: the group wins — they know.
    expect(isEligible(smart, { ...ctx, signals: sig(6, 3) }, {})).toBe(false);
  });
  it('degrades to page-local evidence when signals are absent', () => {
    expect(isEligible(smart, { count: 9, canCreate: true }, {})).toBe(true);
    expect(isEligible(smart, { count: 2, canCreate: true }, {})).toBe(false);
  });
  it('signals present but thin means not offered — no noise on quiet users', () => {
    expect(isEligible(smart, { ...ctx, signals: sig(2, 0) }, {})).toBe(false);
  });
});
