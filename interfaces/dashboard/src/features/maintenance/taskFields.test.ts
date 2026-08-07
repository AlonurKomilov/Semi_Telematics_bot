/**
 * The maintenance form's arithmetic — pinned BEFORE the detail sheet moves.
 *
 * The form holds PERIODS ("due in 3,850 miles"); a saved task holds an
 * ABSOLUTE target ("due at 412,000 mi"). Opening a task converts one to
 * the other, and getting that wrong is not a crash — it is a plausible
 * wrong number in a field an operator then saves.
 *
 * Written before stage 3 of splitting Tasks.tsx rather than after,
 * deliberately. Stage 2 moved fifteen state hooks with 707 tests passing
 * and shipped a silent bug anyway, because nothing asserted the
 * behaviour it changed. These assert the part of the seeding that is
 * pure, so the move has something to fail against.
 */
import { describe, it, expect } from 'vitest';
import { initialTriggerMode, centsToDollars, remainingFrom } from './taskFields';

describe('initialTriggerMode', () => {
  it('prefers date, then miles, then hours', () => {
    expect(initialTriggerMode({ due_date: '2026-09-01', due_miles: 400000 })).toBe('date');
    expect(initialTriggerMode({ due_miles: 400000, due_engine_hours: 9000 })).toBe('miles');
    expect(initialTriggerMode({ due_engine_hours: 9000 })).toBe('hours');
  });

  it('falls back to date so the drawer never opens on a blank view', () => {
    expect(initialTriggerMode({})).toBe('date');
    expect(initialTriggerMode({ due_date: null, due_miles: null })).toBe('date');
  });

  // 0 miles is a REAL target ("due at the next whole mile") — treating it
  // as absent would drop the task onto the wrong tab.
  it('treats a zero target as present, not missing', () => {
    expect(initialTriggerMode({ due_miles: 0 })).toBe('miles');
    expect(initialTriggerMode({ due_engine_hours: 0 })).toBe('hours');
  });
});

describe('centsToDollars', () => {
  it('drops a trailing .00 and keeps real cents', () => {
    expect(centsToDollars(25000)).toBe('250');
    expect(centsToDollars(25050)).toBe('250.50');
    expect(centsToDollars(99)).toBe('0.99');
  });

  it('is empty when there is no cost, but zero is a cost', () => {
    expect(centsToDollars(null)).toBe('');
    expect(centsToDollars(undefined)).toBe('');
    expect(centsToDollars(0)).toBe('0');
  });
});

describe('remainingFrom', () => {
  it('turns an absolute target into what is left', () => {
    expect(remainingFrom(412000, 408150)).toBe('3850');
    expect(remainingFrom(9000, 8750)).toBe('250');
  });

  // The field means "how much further", and there is no such thing as
  // minus 200 miles to go. An overdue task reads 0, not a negative.
  it('clamps an overdue task at zero', () => {
    expect(remainingFrom(412000, 412200)).toBe('0');
  });

  // A live odometer arrives fractional; the input is whole units.
  it('rounds, because the reading is fractional and the field is not', () => {
    expect(remainingFrom(412000, 408149.6)).toBe('3850');
    expect(remainingFrom(9000, 8750.4)).toBe('250');
  });

  // No snapshot yet: show the raw target rather than nothing, so the
  // field is never mysteriously blank while the odometer is in flight.
  it('shows the absolute target when there is no current reading', () => {
    expect(remainingFrom(412000, null)).toBe('412000');
    expect(remainingFrom(412000, undefined)).toBe('412000');
  });

  it('is empty when there is no target at all', () => {
    expect(remainingFrom(null, 408150)).toBe('');
    expect(remainingFrom(undefined, null)).toBe('');
    // 0 is "no target" here on purpose — it is what the old inline code
    // did (`t.due_miles ? … : ''`), and a task due at odometer zero is
    // not a thing. Pinned so the move cannot quietly change it.
    expect(remainingFrom(0, 100)).toBe('');
  });
});
