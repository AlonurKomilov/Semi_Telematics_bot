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

import { POI_LAYERS, esc, glyphSvg, osmPopup, readableOn, vendorPopup } from './poiLayers';

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
  it('clears 3:1 for the mark and the tick, which are what sits on the fill', () => {
    // 1.4.11, not 1.4.3: what sits on a layer's colour is the marker's
    // glyph and the switch's tick — graphics that identify a control,
    // not text.  The COUNT deliberately does not sit there at all,
    // because the violet weigh-station colour cannot reach 4.5:1
    // against any ink (its best is 4.46:1) — see index.css.
    for (const def of POI_LAYERS) {
      const r = ratio(readableOn(def.color), def.color);
      expect(r, `${def.label} (${def.color})`).toBeGreaterThanOrEqual(3);
    }
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
