import { describe, expect, it, vi } from 'vitest';

import { readPendingSelect, sharedOrOwn, toOverlayFixes, toOverlayVehicles } from './bridge';

describe('what crosses into a page we do not own', () => {
  it('carries a marker and its card, and nothing else', () => {
    // The line moved once, deliberately: the on-map card needs to say
    // WHICH truck (company — unit numbers repeat) and WHEN the position
    // was taken (updated_at — a marker with no age reads as live), and
    // the map draws a heading but never a speed.  It moved by exactly
    // those three.  Fuel, DEF, the address, the faults and the registry
    // id stayed behind the "Open in 4truck" button, where the page
    // cannot read them.
    const [v] = toOverlayVehicles([{
      geometry: { coordinates: [-93.72, 35.5] },
      properties: {
        id: 42, name: '103', status: 'moving', heading: 270,
        company: 'PTG', speed_mph: 61.2, updated_at: '2026-09-08T07:00:00Z',
        // Still no business inside google.com/maps:
        address: '515 Marshall Street, Paterson, NJ', fuel_percent: 45,
        def_percent: 78, fault_count: 3, registry_id: 9001,
      },
    }]);
    expect(v).toEqual({
      id: '42', name: '103', lat: 35.5, lng: -93.72, status: 'moving', heading: 270,
      company: 'PTG', speed_mph: 61.2, updated_at: '2026-09-08T07:00:00Z',
    });
    expect(Object.keys(v)).toHaveLength(9);
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

describe('the live feed, trimmed', () => {
  it('keeps a marker\'s worth per fix', () => {
    expect(toOverlayFixes({ positions: { '42': { lat: 35.5, lng: -93.7, speed_mph: 62, heading: 270 } } }))
      .toEqual([{ id: '42', lat: 35.5, lng: -93.7, speed_mph: 62, heading: 270 }]);
  });

  it('drops a fix with no usable position — including the null that Number() turns into 0', () => {
    // Number(null) is 0, a finite coordinate off the coast of Africa.
    expect(toOverlayFixes({ positions: {
      a: { lat: null, lng: -93 },
      b: { lng: -93 },
      c: { lat: 35, lng: null },
      d: { lat: 'north', lng: -93 },
    } })).toEqual([]);
  });

  it('a missing speed is standing still, a missing heading is unknown — never NaN', () => {
    const [f] = toOverlayFixes({ positions: { '7': { lat: 1, lng: 2 } } });
    expect(f.speed_mph).toBe(0);
    expect(f.heading).toBeNull();
  });

  it('survives an empty or malformed payload', () => {
    expect(toOverlayFixes({})).toEqual([]);
    expect(toOverlayFixes(undefined as never)).toEqual([]);
  });
});

describe('sharedOrOwn', () => {
  it('takes the shared answer when the worker has one', async () => {
    const direct = vi.fn(async () => 'own');
    expect(await sharedOrOwn(async () => ({ ok: true, wire: 'shared' }), direct)).toBe('shared');
    expect(direct).not.toHaveBeenCalled();
  });

  it('asks for its own when the worker cannot answer', async () => {
    // Asleep, updating, signed out at that moment — the caller does not
    // need to know which, only that sharing bought nothing this time.
    const direct = vi.fn(async () => 'own');
    expect(await sharedOrOwn(async () => ({ ok: false }), direct)).toBe('own');
    expect(direct).toHaveBeenCalledTimes(1);
  });

  it('lets a failure of its own reach the caller', async () => {
    // The panel's poll swallows errors on purpose; that decision lives
    // at the call site, not in here.
    await expect(sharedOrOwn(
      async () => ({ ok: false }),
      async () => { throw new Error('offline'); },
    )).rejects.toThrow('offline');
  });
});

describe('toOverlayVehicles — the card\'s fields', () => {
  const feature = (props: Record<string, unknown>) => ({
    geometry: { coordinates: [-87, 41] as [number, number] },
    properties: props,
  });

  it('carries what the card answers with, and nothing more', () => {
    const [v] = toOverlayVehicles([feature({
      id: 'v1', name: '229', company: 'RMR', status: 'moving', heading: 90,
      speed_mph: 56.4, updated_at: '2026-09-08T07:00:00Z',
      // Present in the map payload, deliberately not handed to a page
      // we do not control:
      fuel_percent: 36, def_percent: 75,
      address: '123 Main St', registry_id: 60, source: 'samsara', fault_count: 2,
    })]);
    expect(v).toEqual({
      id: 'v1', name: '229', lat: 41, lng: -87, status: 'moving', heading: 90,
      company: 'RMR', speed_mph: 56.4, updated_at: '2026-09-08T07:00:00Z',
    });
  });

  it('a field the payload never sent is empty, never invented', () => {
    // `Number(undefined)` is NaN; a card that read it as 0 would say a
    // truck was stopped when nobody had measured it.
    const [v] = toOverlayVehicles([feature({ id: 'v2', name: '101' })]);
    expect(v.speed_mph).toBe(0);
    expect(v.company).toBe('');
    expect(v.updated_at).toBe('');
  });
});

describe('the truck handed from the map to the panel', () => {
  it('carries an identity each feature can resolve', () => {
    expect(readPendingSelect({ id: '42', name: '103', company: 'PTG', fromMap: false }))
      .toEqual({ id: '42', name: '103', company: 'PTG', fromMap: false });
  });

  it('still reads the bare string the key held before Inventory existed', () => {
    // An update lands while a click may already be sitting in storage.
    // Dropping it would eat that click, and the person would press the
    // button twice and blame the panel.
    expect(readPendingSelect('42')).toEqual({ id: '42', name: '', company: '', fromMap: false });
  });

  it('accepts a name with no id — the half Inventory actually matches on', () => {
    expect(readPendingSelect({ name: '103' }))
      .toEqual({ id: '', name: '103', company: '', fromMap: false });
  });

  it('refuses everything that identifies nothing', () => {
    for (const v of [undefined, null, '', 0, {}, { id: '' }, { id: 7 }, []]) {
      expect(readPendingSelect(v), JSON.stringify(v) ?? 'undefined').toBeNull();
    }
  });
});

describe('what is aboard, on a page we do not own', () => {
  const feature = (registry_id: number) => ({
    geometry: { coordinates: [-93.72, 35.5] },
    properties: { id: '42', name: '103', status: 'moving', registry_id },
  });

  it('carries counts when the panel is on Inventory — and never the key it joined on', () => {
    const counts = new Map([[9001, { total: 3, attention: 1 }]]);
    const [v] = toOverlayVehicles([feature(9001)] as never, counts);
    expect(v.inventory_total).toBe(3);
    expect(v.inventory_attention).toBe(1);
    // registry_id is how the join was made; it is not payload.
    expect('registry_id' in v).toBe(false);
    // Counts, never contents: no label, no serial, no note.
    for (const forbidden of ['label', 'identifier', 'notes', 'items']) {
      expect(forbidden in v, forbidden).toBe(false);
    }
  });

  it('says nothing at all when the panel is not on Inventory', () => {
    const [v] = toOverlayVehicles([feature(9001)] as never);
    expect('inventory_total' in v).toBe(false);
    expect(Object.keys(v)).toHaveLength(9);
  });

  it('carries a ZERO when the question was asked', () => {
    // The line moved once, deliberately.  It used to drop zeros, from a
    // time when the map's counts and the panel's list were one answer
    // and a zero could reach a Live Map user as "0 items" on every
    // marker.  They are separate questions now — the worker only builds
    // this map at all while the panel is on Inventory — and there a
    // zero is the ANSWER: somebody who switched features and clicked a
    // truck is owed "nothing recorded", not a silence they must read as
    // either empty or still-loading.
    //
    // The guard is the map's EXISTENCE, pinned by the case below.
    const [v] = toOverlayVehicles([feature(9001)] as never,
      new Map([[9001, { total: 0, attention: 0 }]]));
    expect(v.inventory_total).toBe(0);
  });

  it('leaves a truck the counts do not mention alone', () => {
    // Absent, not zero: a card should say "3 items" or say nothing —
    // announcing an emptiness nobody asked about is noise.
    const [v] = toOverlayVehicles([feature(7)] as never, new Map([[9001, { total: 3, attention: 1 }]]));
    expect('inventory_total' in v).toBe(false);
  });
});

describe('a choice made ON Google’s map says so', () => {
  it('carries the flag when the overlay sets it', () => {
    expect(readPendingSelect({ id: '42', name: '103', company: 'PTG', fromMap: true }))
      .toEqual({ id: '42', name: '103', company: 'PTG', fromMap: true });
  });

  it('treats an absent flag as false, not as unknown', () => {
    // A choice written before this field existed came from the panel's
    // own list, where following a Google Maps tab is the right answer.
    expect(readPendingSelect({ id: '42', name: '103', company: 'PTG' })?.fromMap).toBe(false);
    expect(readPendingSelect('42')?.fromMap).toBe(false);
  });

  it('does not take a truthy string for a yes', () => {
    expect(readPendingSelect({ id: '42', fromMap: 'yes' })?.fromMap).toBe(false);
  });
});
