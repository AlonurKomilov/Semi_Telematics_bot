/**
 * One line, one separator, one string — the text and the tooltip that
 * explains it cannot be composed differently if they are composed once.
 */
import { describe, expect, it } from 'vitest';

import { vehicleLine } from './vehicleLabel';

describe('a vehicle on one line', () => {
  it('adds the company when there is one', () => {
    expect(vehicleLine('704', 'Premier Trucking Group')).toBe('704 · Premier Trucking Group');
  });

  it('says just the unit when there is not', () => {
    expect(vehicleLine('704')).toBe('704');
    expect(vehicleLine('704', null)).toBe('704');
    expect(vehicleLine('704', '')).toBe('704');
  });

  it('holds the company back when one company is all there is', () => {
    // Live Map only names the company when the account HAS more than one:
    // printing the same company on all 190 rows spends the width that the
    // unit number — the thing being scanned for — is already short of.
    expect(vehicleLine('704', 'Premier Trucking Group', false)).toBe('704');
  });
});
