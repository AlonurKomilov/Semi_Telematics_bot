import { describe, it, expect } from 'vitest';

// The urgency sortKey the Mileage column uses, lifted so the ordering
// contract is testable without mounting a grid.
const urgency = (r: { due_miles: number | null; last_odometer?: number | null }) =>
  r.due_miles == null ? Number.POSITIVE_INFINITY
    : Number(r.due_miles) - Number(r.last_odometer ?? 0);

describe('maintenance opens on urgency', () => {
  it('puts the fewest-miles-to-go truck first, not the highest odometer', () => {
    const rows = [
      { id: 'far',    due_miles: 300_575, last_odometer: 200_000 },  // 100,575 to go
      { id: 'urgent', due_miles: 300_575, last_odometer: 288_729 },  //  11,846 to go
      { id: 'none',   due_miles: null,    last_odometer: 999_999 },  // no threshold
    ];
    const order = [...rows].sort((a, b) => urgency(a) - urgency(b)).map((r) => r.id);
    // 'far' has the LOWER odometer — a raw-mileage sort would rank it
    // first. Urgency must not.
    expect(order).toEqual(['urgent', 'far', 'none']);
  });

  it('puts OVERDUE above due-now — negative is more urgent, not less', () => {
    // The case the first version of this test missed. Remaining goes
    // NEGATIVE once the odometer passes due_miles, and the UI renders it
    // as "5,000 mi overdue" in danger red. A truck already past service
    // outranks one that is exactly due, which outranks one still to come.
    //
    // This guards against the plausible-looking "fix" Math.max(0, …),
    // which would clamp every overdue row to 0 and sink it below every
    // pending task — burying precisely the work that is most late.
    const rows = [
      { id: 'due-now',       due_miles: 100_000, last_odometer: 100_000 },  //      0
      { id: 'very-overdue',  due_miles: 100_000, last_odometer: 105_000 },  // -5,000
      { id: 'not-yet',       due_miles: 100_000, last_odometer:  90_000 },  // +10,000
      { id: 'just-overdue',  due_miles: 100_000, last_odometer: 100_500 },  //   -500
    ];
    expect([...rows].sort((a, b) => urgency(a) - urgency(b)).map((r) => r.id))
      .toEqual(['very-overdue', 'just-overdue', 'due-now', 'not-yet']);
  });

  it('sinks rows with no mileage threshold to the bottom', () => {
    const rows = [
      { id: 'none', due_miles: null, last_odometer: 0 },
      { id: 'due',  due_miles: 1000, last_odometer: 900 },
    ];
    expect([...rows].sort((a, b) => urgency(a) - urgency(b)).map((r) => r.id))
      .toEqual(['due', 'none']);
  });
});
