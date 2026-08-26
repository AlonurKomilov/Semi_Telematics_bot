/**
 * The control does what the callout declares.
 *
 * `kind` is the default, not the decision: the identity questions are
 * conditions like `no_engine_data`, but they carry `dismiss: 'none'`
 * because a question waiting on an answer must not be closeable — the
 * answer buttons are how it leaves.  So the catalog declares it per
 * key.
 *
 * Two behaviours remain, and that is the finding rather than the
 * shape: a `remove` existed for dismissible advice and no callout
 * ever wanted it.  A caveat qualifies a number so it must not be
 * hideable; a condition can come back so it collapses.  See
 * useDismissal's header for the whole account.
 */
import { describe, it, expect } from 'vitest';
import {
  defaultDismiss, dismissBehaviour, CALLOUT_CATALOG,
} from './calloutCatalog';

describe('dismiss behaviour', () => {
  it('defaults by kind', () => {
    expect(defaultDismiss('caveat')).toBe('none');
    expect(defaultDismiss('condition')).toBe('collapse');
  });

  it('protects the data qualifiers — no X on a caveat', () => {
    // Dismissing "these miles are summed across two devices" would
    // hide the correction, not the noise.
    for (const key of Object.keys(CALLOUT_CATALOG)) {
      if (CALLOUT_CATALOG[key].kind === 'caveat') {
        expect(dismissBehaviour(key)).toBe('none');
      }
    }
  });

  it('collapses the no-engine-data condition rather than hiding it', () => {
    expect(dismissBehaviour('vehicle.no_engine_data')).toBe('collapse');
  });

  it('lets a callout override its kind default', () => {
    // The live case: a condition whose answer is a button, not a
    // close — so it opts OUT of the collapse its kind would give it.
    for (const key of ['vehicle.vin_changed', 'vehicle.gateway_swapped',
                       'vehicle.odometer_rebased']) {
      expect(defaultDismiss(CALLOUT_CATALOG[key].kind)).toBe('collapse');
      expect(dismissBehaviour(key)).toBe('none');
    }
  });

  it('shows no X for a key it does not know', () => {
    // An unknown key already renders as a raw string; offering to
    // dismiss it would store an id nothing can ever clear.
    expect(dismissBehaviour('nope.nothing')).toBe('none');
  });
});

/**
 * The icon is part of the promise.
 *
 * An X says "gone" — a promise collapse does not keep, since the line
 * stays on screen.  Pairing the fold-up chevron with the fold-down one
 * the collapsed row already shows makes the control reversible on
 * sight, which is what an X would have hidden.  Nothing renders an X
 * any more, which is the point: no callout removes.
 */
describe('the control icon keeps its promise', () => {
  it('offers a reversible chevron, never an X', () => {
    const iconFor = (b: ReturnType<typeof dismissBehaviour>) =>
      b === 'collapse' ? 'chevron-up' : 'none';
    expect(iconFor(dismissBehaviour('vehicle.no_engine_data'))).toBe('chevron-up');
    expect(iconFor(dismissBehaviour('mileage.partial'))).toBe('none');
    expect(iconFor(dismissBehaviour('vehicle.vin_changed'))).toBe('none');
  });
});
