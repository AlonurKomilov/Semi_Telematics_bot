import { describe, it, expect } from 'vitest';

import { colourFor, findMapSurface, sameSurface } from './surface';

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
