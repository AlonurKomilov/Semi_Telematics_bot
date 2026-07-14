import { describe, it, expect } from 'vitest';
import { defaultOsmPopup } from './usePoiLayers';
import { vendorDirectoryPopup } from '../config/poiLayers';
import type { PoiFeature, PoiLayerDef } from '../config/poiLayers';

/**
 * Popup-builder contract: the engine renders `def.popup?.(f, def) ??
 * defaultOsmPopup(f, def)`.  These tests pin the default (OSM-tag)
 * builder so a DB-backed layer authored with a custom `popup` can rely
 * on the fallback staying OSM-only — layer-specific display logic
 * belongs in the layer def, never inside usePoiLayers.
 */

const DEF: PoiLayerDef = {
  id: 'fuel_station',
  label: 'Fuel Stations',
  color: '#f59e0b',
  icon: '⛽',
  defaultOn: false,
};

function feat(properties: Record<string, unknown>): PoiFeature {
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [-87.65, 41.85] },
    properties,
  };
}

describe('defaultOsmPopup', () => {
  it('renders the feature name with the brand as subtitle', () => {
    const html = defaultOsmPopup(
      feat({ name: 'Pilot Travel Center #123', brand: 'Pilot' }), DEF,
    );
    expect(html).toContain('Pilot Travel Center #123');
    expect(html).toContain('Pilot');
  });

  it('falls back to the layer label when the feature has no name', () => {
    const html = defaultOsmPopup(feat({}), DEF);
    expect(html).toContain('Fuel Stations');
  });

  it('renders amenity badges from OSM tags', () => {
    const html = defaultOsmPopup(
      feat({ name: 'X', 'fuel:diesel': 'yes', 'fuel:adblue': 'yes', shower: 'yes' }),
      DEF,
    );
    expect(html).toContain('Diesel');
    expect(html).toContain('DEF');
    expect(html).toContain('Showers');
  });

  it('omits badge/meta sections when no matching tags exist', () => {
    const html = defaultOsmPopup(feat({ name: 'Plain Stop' }), DEF);
    expect(html).not.toContain('Diesel');
    expect(html).not.toContain('🕐');
  });

  it('vendorDirectoryPopup renders identity fields, service badges, and escapes HTML', () => {
    const html = vendorDirectoryPopup(feat({
      name: 'Joe <b>Truck</b> Repair',
      services: 'Brakes, Tires, PM Service',
      address: '1 Main St, Springfield, IL',
      phone: '555-0100',
      website: 'https://joestruck.example',
    }));
    expect(html).toContain('Joe &lt;b&gt;Truck&lt;/b&gt; Repair');   // escaped, not injected
    expect(html).toContain('Brakes');
    expect(html).toContain('Tires');
    expect(html).toContain('1 Main St, Springfield, IL');
    expect(html).toContain('joestruck.example');
    // Identity-only contract: never ratings/usage counts on the map popup.
    expect(html).not.toMatch(/rating|review|companies/i);
  });

  it('vendorDirectoryPopup omits sections that have no data and rejects non-http websites', () => {
    const html = vendorDirectoryPopup(feat({ name: 'Bare Shop', website: 'javascript:alert(1)' }));
    expect(html).toContain('Bare Shop');
    expect(html).not.toContain('javascript:');
    expect(html).not.toContain('<a ');
  });

  it('a custom def.popup builder receives the feature and wins over the default shape', () => {
    const custom: PoiLayerDef = {
      ...DEF,
      id: 'vendor_directory',
      label: 'Repair Shops',
      popup: (f, d) => `<b>${String(f.properties.name)}</b> · ${d.label}`,
    };
    // The engine dispatches `def.popup ? def.popup(f, def) : defaultOsmPopup(...)`;
    // pin the builder's call contract here.
    const html = custom.popup!(feat({ name: 'Joe Truck Repair' }), custom);
    expect(html).toBe('<b>Joe Truck Repair</b> · Repair Shops');
  });
});
