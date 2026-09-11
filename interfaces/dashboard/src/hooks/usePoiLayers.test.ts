/**
 * The popup is the one place on the map where a stranger chooses the
 * text.  Every value in it is an OpenStreetMap tag, and OSM is editable
 * by anybody — so a fuel station can be named `<img onerror=…>`.
 *
 * This test exists because the default popup interpolated those raw
 * into `innerHTML` for as long as it had existed, while the vendor
 * popup beside it escaped from day one.  Nothing about that was
 * deliberate, and nothing warned about it.
 */
import { describe, expect, it } from 'vitest';

import { defaultOsmPopup } from './usePoiLayers';
import { vendorDirectoryPopup, type PoiFeature, type PoiLayerDef } from '../config/poiLayers';

const EVIL = '<img src=x onerror="alert(1)">';

const feature = (properties: Record<string, unknown>): PoiFeature => ({
  type: 'Feature', geometry: { type: 'Point', coordinates: [-88, 41] }, properties,
});

const def: PoiLayerDef = {
  id: 'fuel_station', label: 'Fuel Stations', color: '#f59e0b',
  icon: 'x', defaultOn: false,
};

describe('a popup never hands OSM text to innerHTML', () => {
  it('escapes the name', () => {
    const html = defaultOsmPopup(feature({ name: EVIL }), def);
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;img');
  });

  it('escapes the subtitle, which is brand or operator', () => {
    expect(defaultOsmPopup(feature({ name: 'Shell', brand: EVIL }), def)).not.toContain('<img');
    expect(defaultOsmPopup(feature({ name: 'Shell', operator: EVIL }), def)).not.toContain('<img');
  });

  it('escapes the fields that ride in badges and meta', () => {
    const html = defaultOsmPopup(
      feature({ name: 'Ok', capacity: EVIL, opening_hours: EVIL, phone: EVIL }), def);
    expect(html).not.toContain('<img');
  });

  it('holds the vendor popup to the same rule', () => {
    const html = vendorDirectoryPopup(feature({
      name: EVIL, chain: EVIL, address: EVIL, phone: EVIL,
      services: EVIL, my_vendor_name: EVIL, website: `https://x.example/${EVIL}`,
    }));
    expect(html).not.toContain('<img');
  });

  it('refuses a website that is not http(s) rather than rendering the href', () => {
    // `javascript:` in an href is the other way a tag value becomes code.
    const html = vendorDirectoryPopup(feature({ name: 'Ok', website: 'javascript:alert(1)' }));
    expect(html).not.toContain('javascript:');
  });
});
