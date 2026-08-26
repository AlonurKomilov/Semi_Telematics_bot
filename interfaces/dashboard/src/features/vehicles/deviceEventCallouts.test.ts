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

  it('fold, but are never removed — the question stays on screen', () => {
    // The fear this once encoded was real and aimed at the wrong
    // control: hiding a pending identity question would leave a
    // truck's history filed under the wrong unit with nothing on
    // screen to say so.  Removal would do that.  Collapse does not —
    // it leaves the statement as one line carrying its count, re-opens
    // when a new truck raises the same question, and undoes in a
    // click.  Someone who is not the person answering these should not
    // carry the queue at the top of their page all week.
    for (const key of EVENT_KEYS) {
      expect(dismissBehaviour(key)).toBe('collapse');
    }
    // The guarantee that matters is that 'remove' does not exist at
    // all — no callout can be hidden outright, whatever it declares.
    const behaviours = Object.keys(CALLOUT_CATALOG)
      .map((k) => dismissBehaviour(k));
    expect(behaviours).not.toContain('remove');
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
