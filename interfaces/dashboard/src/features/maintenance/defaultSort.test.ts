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

  it('sinks rows with no mileage threshold to the bottom', () => {
    const rows = [
      { id: 'none', due_miles: null, last_odometer: 0 },
      { id: 'due',  due_miles: 1000, last_odometer: 900 },
    ];
    expect([...rows].sort((a, b) => urgency(a) - urgency(b)).map((r) => r.id))
      .toEqual(['due', 'none']);
  });
});
