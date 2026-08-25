/**
 * Device questions render through the callouts lane — display only.
 *
 * The identity watch keeps its own store, its own detector and its own
 * answer flow: "Different truck…" performs registry surgery, which the
 * callouts capability must never learn about.  What moved is the
 * SHAPE, so a page does not carry two kinds of statement.
 */
import { describe, it, expect } from 'vitest';
import { CALLOUT_CATALOG, dismissBehaviour } from '../../components/callouts';

const EVENT_KEYS = [
  'vehicle.vin_changed',
  'vehicle.gateway_swapped',
  'vehicle.odometer_rebased',
];

describe('device-event callouts', () => {
  it('are registered in the catalog', () => {
    // Unregistered keys render as raw strings; the drift guard in
    // test_callouts.py enforces the backend half of the same seam.
    for (const key of EVENT_KEYS) {
      expect(CALLOUT_CATALOG[key]).toBeDefined();
    }
  });

  it('carry no X, because they are answered rather than hidden', () => {
    // Their answer edits the registry.  Offering to hide the question
    // would leave a truck's history filed under the wrong unit with
    // nothing on screen to say so — and the card supplies its own
    // Dismiss for the rows that ask nothing.
    for (const key of EVENT_KEYS) {
      expect(dismissBehaviour(key)).toBe('none');
    }
  });

  it('treats a VIN change as the most serious of the three', () => {
    // A changed VIN means the unit may now name a DIFFERENT TRUCK, so
    // every mile and inspection filed under it is in question.
    expect(CALLOUT_CATALOG['vehicle.vin_changed'].severity).toBe('danger');
    expect(CALLOUT_CATALOG['vehicle.gateway_swapped'].severity).toBe('warn');
  });
});
