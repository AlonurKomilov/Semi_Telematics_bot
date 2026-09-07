import { describe, it, expect } from 'vitest';

import { toOverlayVehicles } from './bridge';

describe('what crosses into a page we do not own', () => {
  it('carries a marker\'s worth and nothing else', () => {
    const [v] = toOverlayVehicles([{
      geometry: { coordinates: [-93.72, 35.5] },
      properties: {
        id: 42, name: '103', status: 'moving', heading: 270,
        // Everything below is in the map payload and has no business
        // inside google.com/maps.
        address: '515 Marshall Street, Paterson, NJ', fuel_percent: 45,
        def_percent: 78, fault_count: 3, company: 'PTG', registry_id: 9001,
      },
    }]);
    expect(v).toEqual({ id: '42', name: '103', lat: 35.5, lng: -93.72, status: 'moving', heading: 270 });
    expect(Object.keys(v)).toHaveLength(6);
  });

  it('drops a feature with no usable position rather than drawing at null island', () => {
    expect(toOverlayVehicles([
      { geometry: { coordinates: undefined }, properties: { id: 1 } },
      { properties: { id: 2 } },
      { geometry: { coordinates: [NaN, 35.5] }, properties: { id: 3 } },
    ])).toEqual([]);
  });

  it('survives a payload that is empty or malformed', () => {
    expect(toOverlayVehicles([])).toEqual([]);
    expect(toOverlayVehicles(undefined as never)).toEqual([]);
  });

  it('falls back to a name, then a coordinate, for a feature with no id', () => {
    const vs = toOverlayVehicles([
      { geometry: { coordinates: [-93, 35] }, properties: { name: '104' } },
      { geometry: { coordinates: [-94, 36] }, properties: {} },
    ]);
    expect(vs[0].id).toBe('104');
    expect(vs[1].id).toBe('36,-94');
  });

  it('keeps a heading only when it is a real number', () => {
    const vs = toOverlayVehicles([
      { geometry: { coordinates: [-93, 35] }, properties: { heading: 0 } },
      { geometry: { coordinates: [-93, 35] }, properties: { heading: 'north' } },
      { geometry: { coordinates: [-93, 35] }, properties: {} },
    ]);
    expect(vs.map((v) => v.heading)).toEqual([0, null, null]);
  });
});
