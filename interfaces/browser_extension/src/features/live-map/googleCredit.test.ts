/**
 * The attribution is a licence term, not a flourish — and the query
 * that fetches it has two clamps that are easy to get wrong.
 */
import { describe, expect, it } from 'vitest';

import { viewportQuery } from './googleCredit';

const params = (s: string) => Object.fromEntries(new URLSearchParams(s));

describe('the viewport query Google is asked with', () => {
  it('carries the four edges and the zoom', () => {
    const p = params(viewportQuery({ north: 42, south: 41, east: -87, west: -88 }, 9));
    expect(p).toMatchObject({ zoom: '9', north: '42', south: '41', east: '-87', west: '-88' });
  });

  it('clamps latitude to what Web Mercator has', () => {
    // There are no poles on this projection, and the service refuses
    // anything past ±85.
    const p = params(viewportQuery({ north: 89.9, south: -89.9, east: 10, west: -10 }, 3));
    expect(p.north).toBe('85');
    expect(p.south).toBe('-85');
  });

  it('leaves longitude alone', () => {
    // A map dragged past the date line reports honest out-of-range
    // values, and the service handles them; clamping would move the
    // view Google is being asked about.
    const p = params(viewportQuery({ north: 1, south: 0, east: 190, west: -190 }, 3));
    expect(p.east).toBe('190');
    expect(p.west).toBe('-190');
  });

  it('rounds and clamps the zoom into the range the API takes', () => {
    expect(params(viewportQuery({ north: 1, south: 0, east: 1, west: 0 }, 9.6)).zoom).toBe('10');
    expect(params(viewportQuery({ north: 1, south: 0, east: 1, west: 0 }, -2)).zoom).toBe('0');
    expect(params(viewportQuery({ north: 1, south: 0, east: 1, west: 0 }, 30)).zoom).toBe('22');
  });
});
