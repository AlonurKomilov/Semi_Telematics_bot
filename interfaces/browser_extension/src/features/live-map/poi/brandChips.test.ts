/**
 * A CHIP IS A CLAIM ABOUT WHO OWNS THE PUMP.
 *
 * The fuel layer now admits Petro-Canada by measurement (147 of its 173
 * tagged stations sell diesel) instead of deleting it, because the
 * owner's rule is that a real place gets filtered by the user rather
 * than removed by us.  That only works if the filter tells the truth
 * about which chain a point belongs to.
 *
 * It did not.  `wordMatch` treats a hyphen as a word boundary — its own
 * docstring gives `wordMatch('TA-Petro #145', 'TA') === true` as the
 * desired behaviour — so the `TA / Petro` chip's bare `Petro` term also
 * matches `Petro-Canada`, a different company on a different continent.
 * Someone filtering to TA / Petro would be shown 866 Canadian stations
 * under an American chain's name.
 */
import { describe, it, expect } from 'vitest';

import { POI_LAYERS } from './layers';
import { brandMatch } from './viewport';

type Feature = Parameters<typeof brandMatch>[1];

function pointOf(brand: string): Feature {
  return { properties: { brand, name: brand } } as unknown as Feature;
}

function chipsOfFuelLayer() {
  const fuel = POI_LAYERS.find((l) => l.id === 'fuel_station');
  if (!fuel?.brands?.length) throw new Error('fuel_station has no brand chips');
  return fuel.brands;
}

/** Which chips claim this brand value. */
function claimedBy(brand: string): string[] {
  return chipsOfFuelLayer()
    .filter((b) => brandMatch(b.matchTerms ?? [b.value], pointOf(brand)))
    .map((b) => b.value);
}

describe('fuel brand chips', () => {
  it('shows Petro-Canada under its own name and not under TA / Petro', () => {
    expect(claimedBy('Petro-Canada')).toEqual(['petro_canada']);
    expect(claimedBy('Petro Canada')).toEqual(['petro_canada']);
  });

  it('still shows the American Petro under TA / Petro', () => {
    expect(claimedBy('Petro')).toContain('ta_petro');
    expect(claimedBy('TA')).toContain('ta_petro');
  });

  it('gives every brand the layer admits on its NAME a chip to be found by', () => {
    // The backend allowlist (features/live_map/poi/layers.py) admits these
    // on the brand alone, so each is a chain a driver may want to isolate.
    // A brand with no chip is drawn but cannot be filtered to — which is
    // the whole point of admitting it rather than deleting it.
    for (const brand of ['Petro-Canada', 'Kwik Trip', 'Kwik Star', 'Kwik Fill',
                         'Maverik', "Love's", 'Pilot', 'Flying J', 'TA',
                         'Petro', 'Sapp Bros.', 'Road Ranger']) {
      expect(claimedBy(brand), `${brand} has no chip`).not.toHaveLength(0);
    }
  });

  it('no chip claims a brand that is a different company', () => {
    for (const other of ['Petro Seven', 'Petro Bras', 'PetroUS', 'Petro Mart',
                         'Kwik Shop', 'Kwik Sak']) {
      expect(claimedBy(other), `${other} is claimed by a chip`).toHaveLength(0);
    }
  });
});
