/**
 * A layer's mark and its count sit ON the layer's own colour, so the
 * ink has to be chosen per colour rather than assumed.
 *
 * This file exists because the assumption was made and was wrong: white
 * on the amber fuel colour is 2.15:1 and white on the cyan rest-area
 * colour is 2.43:1, against 4.5:1 for text and 3:1 for a mark that
 * identifies a control.  Seven of eight layers failed; the eighth was
 * blue, which is why nobody noticed.
 */
import { describe, expect, it } from 'vitest';

import {
  POI_LAYERS, SOURCE_STALE_DAYS, esc, glyphSvg, osmPopup, readableOn,
  staleSourceAge, vendorPopup,
} from './layers';

function ratio(a: string, b: string): number {
  const lum = (hex: string) => {
    const h = hex.replace('#', '');
    const ch = (i: number) => {
      const c = parseInt(h.slice(i, i + 2), 16) / 255;
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * ch(0) + 0.7152 * ch(2) + 0.0722 * ch(4);
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe('every layer’s ink can be read on its own colour', () => {
  it('clears 4.5:1 on every layer — the count pill carries TEXT', () => {
    // The tightest is the violet weigh station at 4.68:1, and it passes
    // only because the dark ink is #0a0a0a.  Against the panel's own
    // ground (#0f1115) the same colour measures 4.46 and fails — which
    // is where this landed first, and why the ink is the dashboard's
    // shared constant rather than the one that looked like it belonged.
    for (const def of POI_LAYERS) {
      const r = ratio(readableOn(def.color), def.color);
      expect(r, `${def.label} (${def.color})`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('uses the same dark ink the dashboard measured against', () => {
    // A different near-black is a different verdict; see above.
    const inks = new Set(POI_LAYERS.map((d) => readableOn(d.color)));
    for (const ink of inks) expect(['#0a0a0a', '#ffffff']).toContain(ink);
  });

  it('picks the better of the two inks rather than guessing a threshold', () => {
    // The amber fuel colour is the case that caught it: a 0.45
    // luminance cut handed it white at 2.15:1 when dark gives 8.8:1.
    for (const def of POI_LAYERS) {
      const chosen = ratio(readableOn(def.color), def.color);
      const other = ratio(readableOn(def.color) === '#ffffff' ? '#0f1115' : '#ffffff', def.color);
      expect(chosen, def.label).toBeGreaterThanOrEqual(other);
    }
  });

  it('falls back to the light ink for a colour it cannot parse', () => {
    // Custom layers carry a DB-stored colour; a malformed one must not
    // throw on a map that is mid-draw, and must not return a
    // three-digit shorthand that reads back as NaN downstream.
    expect(readableOn('nonsense')).toBe('#ffffff');
    expect(readableOn('')).toBe('#ffffff');
    expect(readableOn('#fff')).toBe('#ffffff');
  });
});

describe('a popup never hands OpenStreetMap text to innerHTML', () => {
  const EVIL = '<img src=x onerror="alert(1)">';
  const feature = (properties: Record<string, unknown>) => ({ properties });
  const def = POI_LAYERS[0];

  it('escapes the name, the brand and the operator', () => {
    for (const key of ['name', 'brand', 'operator']) {
      const html = osmPopup(feature({ name: 'Ok', [key]: EVIL }), def);
      expect(html, key).not.toContain('<img');
    }
  });

  it('escapes the badge and meta fields', () => {
    const html = osmPopup(
      feature({ name: 'Ok', capacity: EVIL, opening_hours: EVIL, phone: EVIL }), def);
    expect(html).not.toContain('<img');
  });

  it('holds the vendor popup to the same rule', () => {
    const html = vendorPopup(feature({
      name: EVIL, chain: EVIL, address: EVIL, phone: EVIL,
      services: EVIL, my_vendor_name: EVIL,
    }));
    expect(html).not.toContain('<img');
  });

  it('refuses a website that is not http(s) rather than rendering the href', () => {
    expect(vendorPopup(feature({ name: 'Ok', website: 'javascript:alert(1)' })))
      .not.toContain('javascript:');
  });

  it('escapes everywhere, including the shared helper', () => {
    expect(esc('<b>&"')).toBe('&lt;b&gt;&amp;&quot;');
  });
});

describe('a glyph is a string, and an unknown one is empty rather than broken', () => {
  it('draws the built-in marks', () => {
    expect(glyphSvg('fuel', 12)).toContain('<svg');
  });

  it('returns nothing for a custom layer’s emoji, so the caller draws it as text', () => {
    expect(glyphSvg('🚚', 12)).toBe('');
  });
});

/**
 * "None in this view" is true and incomplete — the sentence the owner
 * read over Chicago.  There may be nothing there; a brand chip they
 * pressed may be hiding it; or the OpenStreetMap extract behind the
 * layer may be months behind, so a truck stop that opened since is
 * simply not in it.  Measured 2026-09-13, the two mirrors the server
 * can reach were stamped 2026-06-01 and 2026-07-28.
 *
 * This decides WHEN that last one is worth saying: always is noise on
 * every quiet row, never leaves the reader blaming the panel.
 */
describe('when the source’s age is worth saying', () => {
  const DAY = 86_400_000;
  const NOW = Date.UTC(2026, 8, 13, 12, 0, 0);
  const daysAgo = (n: number) => new Date(NOW - n * DAY).toISOString();

  it('says nothing without a date, or with one it cannot read', () => {
    expect(staleSourceAge(null, NOW)).toBeNull();
    expect(staleSourceAge(undefined, NOW)).toBeNull();
    expect(staleSourceAge('not a date', NOW)).toBeNull();   // never "NaNd old"
  });

  it('stays quiet while the lag is ordinary', () => {
    expect(staleSourceAge(daysAgo(0), NOW)).toBeNull();
    expect(staleSourceAge(daysAgo(SOURCE_STALE_DAYS - 1), NOW)).toBeNull();
  });

  it('speaks from the threshold onward, in whole days', () => {
    expect(staleSourceAge(daysAgo(SOURCE_STALE_DAYS), NOW)).toBe(`${SOURCE_STALE_DAYS}d`);
    expect(staleSourceAge(daysAgo(104), NOW)).toBe('104d');
  });

  it('never reports a negative age when a clock is skewed forward', () => {
    expect(staleSourceAge(new Date(NOW + 5_000).toISOString(), NOW)).toBeNull();
  });

  it('agrees with the dashboard on the threshold', () => {
    // The two panels describe the same mirror.  One calling 30 days
    // "old" while the other calls it ordinary would have a driver and a
    // dispatcher reading different explanations for one empty layer.
    expect(SOURCE_STALE_DAYS).toBe(14);
  });
});
