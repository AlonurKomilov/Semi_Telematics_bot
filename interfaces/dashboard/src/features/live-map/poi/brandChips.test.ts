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
    // EVERY brand the backend allowlist admits on its name
    // (features/live_map/poi/layers.py) — kept COMPLETE on purpose.  An
    // incomplete list here hides the very thing the test is for: `TA
    // Express` and `Speedco` were both admitted and both unfilterable,
    // and this test said nothing, because neither was written down.
    for (const brand of ['Pilot', 'Flying J', "Love's", 'TA', 'TA Express',
                         'Petro', 'Sapp Bros', 'Sapp Bros.', 'Road Ranger',
                         'AmBest', 'Bosselman', 'Speedco', 'Kwik Trip',
                         'Kwik-Trip', 'Kwik Star', 'Kwik Fill', 'Maverik',
                         'Petro-Canada', 'Petro Canada']) {
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
