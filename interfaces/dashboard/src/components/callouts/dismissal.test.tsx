/**
 * The X does what the callout declares — and only removal is recorded.
 *
 * `kind` is too coarse to decide this: a condition that protects a
 * number the reader is about to act on must COLLAPSE (the one line is
 * what stops a 0-mile truck reading as a real zero), while a condition
 * that is merely good-to-know can REMOVE outright.  So the catalog
 * declares it per key, and `kind` only supplies the default.
 */
import { describe, it, expect } from 'vitest';
import {
  defaultDismiss, dismissBehaviour, CALLOUT_CATALOG,
} from './calloutCatalog';

describe('dismiss behaviour', () => {
  it('defaults by kind', () => {
    expect(defaultDismiss('caveat')).toBe('none');
    expect(defaultDismiss('condition')).toBe('collapse');
    expect(defaultDismiss('guidance')).toBe('remove');
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
    // The future case the owner asked for: not urgent, good to know,
    // deserves a real X.
    const spec = { kind: 'condition' as const, severity: 'info' as const,
                   dismiss: 'remove' as const };
    expect(spec.dismiss ?? defaultDismiss(spec.kind)).toBe('remove');
  });

  it('shows no X for a key it does not know', () => {
    // An unknown key already renders as a raw string; offering to
    // dismiss it would store an id nothing can ever clear.
    expect(dismissBehaviour('nope.nothing')).toBe('none');
  });
});
