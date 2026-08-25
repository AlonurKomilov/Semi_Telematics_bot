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

  it('asks in warn, never danger', () => {
    // Danger is reserved for states that are actively failing (overdue,
    // past-due, error).  These are questions waiting on a person, and
    // a full red strip both overstated them and made the body text
    // hard to read against its own background.
    for (const key of EVENT_KEYS) {
      expect(CALLOUT_CATALOG[key].severity).toBe('warn');
    }
  });
});
