/**
 * A CHIP IS A CLAIM ABOUT WHO OWNS THE PUMP.
 *
 * The fuel layer now admits Petro-Canada by measurement (147 of its 173
 * tagged stations sell diesel) instead of deleting it, because the
 * owner's rule is that a real place gets filtered by the user rather
 * than removed by us.  That only works if the filter tells the truth
 * about which chain a point belongs to.
 *
 * It did not.  `wordMatch` treats a hyphen as a word boundary — that is
 * how 'TA' finds "TA-Petro #145" — so the `TA / Petro` chip's bare
 * `Petro` term also matched `Petro-Canada`, `Petro Seven`, `Petro Bras`
 * and `PetroUS`: four different companies filed under one American
 * chain's name.
 *
 * The twin of this file guards the side panel, whose copy of the
 * matcher lives in
 * interfaces/browser_extension/src/features/live-map/poi/viewport.ts.
 */
import { describe, it, expect } from 'vitest';

import { POI_LAYERS } from './layers';
import { brandMatch } from './usePoiLayers';

function chipsOfFuelLayer() {
  const fuel = POI_LAYERS.find((l) => l.id === 'fuel_station');
  if (!fuel?.brandFilters?.length) throw new Error('fuel_station has no brand chips');
  return fuel.brandFilters;
}

/** Which chips claim a point whose OSM `brand` is this value. */
function claimedBy(brand: string): string[] {
  return chipsOfFuelLayer()
    .filter((b) => brandMatch(b.matchTerms ?? [b.value], brand, brand, ''))
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

  it('falls back to the name only when OSM stated no brand', () => {
    // The reason wordMatch exists: an unbranded node called "TA-Petro #145"
    // is still a TA.  Removing the fallback would lose it.
    const tap = chipsOfFuelLayer().find((b) => b.value === 'ta_petro')!;
    expect(brandMatch(tap.matchTerms!, 'TA-Petro #145', '', '')).toBe(true);
    // …and the same string as a stated BRAND is not a TA, because a brand
    // tag is an identity and "TA-Petro #145" is not one of ours.
    expect(brandMatch(tap.matchTerms!, '', 'Petro-Canada', '')).toBe(false);
  });
});
