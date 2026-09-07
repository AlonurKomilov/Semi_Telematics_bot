import { describe, it, expect } from 'vitest';

import { HIT_RADIUS, colourFor, findMapSurface, markerAt, needsRemeasure, sameSurface } from './surface';

const rect = (left: number, top: number, width: number, height: number) => ({
  getBoundingClientRect: () => ({ left, top, width, height }) as DOMRectReadOnly,
});

describe('finding the map in a page nobody versions', () => {
  it('takes the biggest canvas, which is the map', () => {
    expect(findMapSurface([rect(0, 0, 400, 400), rect(0, 0, 1200, 900)]))
      .toEqual({ left: 0, top: 0, width: 1200, height: 900 });
  });

  it('ignores thumbnails and strips — a Street View preview is not a map', () => {
    expect(findMapSurface([rect(0, 0, 200, 150), rect(10, 10, 300, 80)])).toBeNull();
  });

  it('draws nothing at all when there is no canvas', () => {
    // Which is the correct answer for a Google page we do not recognise:
    // an overlay guessing at a layout it cannot see is worse than none.
    expect(findMapSurface([])).toBeNull();
  });

  it('measures the canvas, not the window — the search panel floats over it', () => {
    // A canvas offset from the origin still reports its own box, so the
    // URL coordinate lands at the canvas centre rather than the screen's.
    expect(findMapSurface([rect(408, 0, 1000, 900)])?.left).toBe(408);
  });
});

describe('when a redraw is worth doing', () => {
  const s = { left: 0, top: 0, width: 1000, height: 800 };
  it('ignores a sub-pixel resize', () => {
    expect(sameSurface(s, { ...s, width: 1000.4 })).toBe(true);
  });
  it('notices a real one', () => {
    expect(sameSurface(s, { ...s, width: 1200 })).toBe(false);
    expect(sameSurface(s, null)).toBe(false);
  });
});

describe('marker colour', () => {
  it('matches the panel and the dashboard', () => {
    expect(colourFor('moving')).toBe('#22c55e');
    expect(colourFor('idle')).toBe('#f59e0b');
    expect(colourFor('stopped')).toBe('#ef4444');
  });
  it('treats an unknown status as stopped rather than drawing nothing', () => {
    expect(colourFor('who-knows')).toBe('#ef4444');
  });
});

describe('which truck a click landed on', () => {
  const drawn = new Map([
    ['a', { x: 100, y: 100 }],
    ['b', { x: 108, y: 100 }],   // overlapping, as two trucks in one yard are
    ['c', { x: 400, y: 400 }],
  ]);

  it('takes the nearest, not the first found — a yard full of trucks must resolve the same way twice', () => {
    expect(markerAt(drawn, 101, 100)).toBe('a');
    expect(markerAt(drawn, 107, 100)).toBe('b');
  });

  it('is stable when the list re-orders', () => {
    const reversed = new Map([...drawn].reverse());
    expect(markerAt(reversed, 107, 100)).toBe(markerAt(drawn, 107, 100));
  });

  it('a click on empty map hits nothing', () => {
    expect(markerAt(drawn, 250, 250)).toBeNull();
  });

  it('forgives a near miss, and only a near one', () => {
    expect(markerAt(drawn, 400 + HIT_RADIUS - 1, 400)).toBe('c');
    expect(markerAt(drawn, 400 + HIT_RADIUS + 1, 400)).toBeNull();
  });

  it('nothing drawn, nothing hit', () => {
    expect(markerAt(new Map(), 100, 100)).toBeNull();
  });
});

describe('needsRemeasure', () => {
  const settled = {
    url: 'https://www.google.com/maps/@41,-87,12z',
    measuredUrl: 'https://www.google.com/maps/@41,-87,12z',
    connected: true,
    geometryDirty: false,
    measuredAt: 1000,
    now: 1120,
    recheckMs: 2000,
  };

  it('measures nothing while the page is holding still', () => {
    expect(needsRemeasure(settled)).toBe(false);
  });

  it('measures when the camera moved', () => {
    expect(needsRemeasure({ ...settled, url: 'https://www.google.com/maps/@41,-87,13z' })).toBe(true);
  });

  it('measures when the canvas we measured has left the page', () => {
    expect(needsRemeasure({ ...settled, connected: false })).toBe(true);
  });

  it('measures when something reported a resize', () => {
    expect(needsRemeasure({ ...settled, geometryDirty: true })).toBe(true);
  });

  it('measures again on the slow beat, resize or not', () => {
    // Google's markup is unversioned; a resize that never reaches us
    // must not leave the box wrong for ever.
    expect(needsRemeasure({ ...settled, now: 3000 })).toBe(true);
  });
});
