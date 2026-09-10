import { describe, it, expect } from 'vitest';

// Imported as text, not read from disk: the tests run in jsdom, where
// `import.meta.url` is an http URL and node's file helpers refuse it.
import overlaySrc from '../../content/mapsOverlay.ts?raw';

import { HIT_RADIUS, TOUCH_HIT_RADIUS, cardAnchor, colourFor, findMapSurface, hitRadiusFor, markerAt, needsRemeasure, sameSurface } from './surface';

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

describe('cardAnchor', () => {
  const surface = { width: 800, height: 600 };
  const box = { width: 220, height: 120 };

  it('sits above the marker, centred on it', () => {
    const a = cardAnchor({ x: 400, y: 300 }, box, surface);
    expect(a.below).toBe(false);
    expect(a.left).toBe(400 - 110);
    expect(a.top).toBe(300 - 120 - 12);
  });

  it('flips below when there is no room above', () => {
    const a = cardAnchor({ x: 400, y: 40 }, box, surface);
    expect(a.below).toBe(true);
    expect(a.top).toBe(40 + 12);
  });

  it('never hangs off an edge — the missing half is always the numbers', () => {
    expect(cardAnchor({ x: 5, y: 300 }, box, surface).left).toBe(4);
    expect(cardAnchor({ x: 795, y: 300 }, box, surface).left).toBe(800 - 220 - 4);
    expect(cardAnchor({ x: 400, y: 595 }, box, surface).top).toBeLessThanOrEqual(600 - 120 - 4);
  });

  it('stays inside a map smaller than the card', () => {
    const tiny = { width: 200, height: 100 };
    const a = cardAnchor({ x: 100, y: 50 }, box, tiny);
    expect(a.left).toBe(4);
    expect(a.top).toBe(4);
  });
});

describe('how big the target is, per pointer', () => {
  it('gives a finger more room than a mouse', () => {
    expect(hitRadiusFor('touch')).toBe(TOUCH_HIT_RADIUS);
    expect(hitRadiusFor('pen')).toBe(TOUCH_HIT_RADIUS);
    expect(hitRadiusFor('touch')).toBeGreaterThan(hitRadiusFor('mouse'));
  });

  it('falls back to the mouse radius when the kind is unknown', () => {
    // pointercancel and the synthetic paths hand us nothing.
    expect(hitRadiusFor(undefined)).toBe(HIT_RADIUS);
    expect(hitRadiusFor('mouse')).toBe(HIT_RADIUS);
    expect(hitRadiusFor('')).toBe(HIT_RADIUS);
  });

  it('stays big enough for the glyph it is aimed at', () => {
    // The arrow draws 18px across, so a radius under 9 could not cover
    // the thing the person can see.  This is the number the anchor
    // below is measured against.
    expect(HIT_RADIUS).toBeGreaterThanOrEqual(9);
  });
});

describe('the marker anchor — the bug this file exists to prevent', () => {
  // A marker is a flex ROW: glyph, gap, name pill.  It was anchored with
  // translate(-50%,-50%), which centres the ROW — so the glyph, at the
  // row's left edge, was drawn 20-40px west of the point markerAt tests
  // against.  Every press missed and fell through to Google; the hover
  // cursor never fired.  With the label hidden at national zoom the row
  // IS the glyph and it worked, which is why it read as a zoom mystery.
  //
  // Two places state the anchor — the base cssText and placeAt — and
  // they must never disagree, so this reads the source rather than the
  // behaviour: jsdom performs no layout and could not see it.
  const src = overlaySrc as unknown as string;

  it('anchors on the glyph in both places, through one constant', () => {
    expect(src).toMatch(/const GLYPH_HALF = \d+;/);
    expect(src).toContain('translate(-${GLYPH_HALF}px,-50%)');
    expect(src).toContain('translate(-${GLYPH_HALF}px, -50%)');
    // The row-centring form must not come back.
    expect(src).not.toContain('translate(-50%, -50%)');
    expect(src).not.toContain('translate(-50%,-50%)');
  });

  it('pins the glyph box the constant is half of', () => {
    // The arrow draws 18 and the dot 16; a fixed box makes the half true
    // by construction rather than by whichever glyph is showing.
    expect(src).toContain('width:18px;justify-content:center');
  });

  it('keeps the row left-to-right, whatever language Google serves', () => {
    // The anchor is the row's LEFT edge; on an RTL document the flex row
    // reverses and every truck would sit a row-width east.
    expect(src).toContain('direction:ltr');
  });
});

describe('whose press it is — the rule the owner asked for', () => {
  const src = overlaySrc as unknown as string;

  it('stops ALL FIVE events a press fires, not only the click', () => {
    // A mouse press fires pointerdown, mousedown, pointerup, mouseup,
    // click.  Google acts on the MOUSE pair; swallowing only the click
    // changed nothing anyone could see — our card opened and Google
    // dropped its pin, one press and two answers.
    for (const ev of ["'pointerdown'", "'mousedown'", "'pointerup'", "'mouseup'", "'click'"]) {
      expect(src, ev).toContain(`window.addEventListener(${ev}`);
    }
    expect(src).toContain('function onMouseCapture');
  });

  it('stops propagation rather than preventing default on the passive path', () => {
    // passive:true forbids preventDefault and nothing else, so capture
    // + stopPropagation is what keeps Google's handlers from running
    // WITHOUT giving up the passive pointer path panning needs.
    expect(src).toMatch(/truckPress = \{ id: hit/);
    const down = src.slice(src.indexOf('function onPointerDown'), src.indexOf('function onMap('));
    expect(down).not.toContain('preventDefault');
  });

  it('holds the press through the release so mouseup and click are covered too', () => {
    // Cleared on the NEXT pointerdown and in the click — not on
    // pointerup, which fires two events before the gesture is over.
    const up = src.slice(src.indexOf('function onPointerUp'), src.indexOf('function teardown'));
    expect(up).toContain('truckPress');
    expect(up).not.toMatch(/truckPress = null/);
    // …and cleared as one of pointerdown's FIRST statements, above every
    // early return: a stamp that outlives its press eats the next click.
    const down = src.slice(src.indexOf('function onPointerDown'));
    expect(down.indexOf('truckPress = null')).toBeLessThan(down.indexOf('if (chip'));
  });

  it('says the hover cursor in CSS, because inline on the canvas loses', () => {
    // The layer is pointer-events:none, so the element under the pointer
    // is always one of Google's and which one is not ours to predict.
    // A rule reaching the container AND its descendants does not have to
    // guess right.
    expect(src).toContain('cursor:pointer !important');
    expect(src).toContain('OVER_TRUCK_CLASS');
    expect(src).not.toContain("canvasEl.style.cursor");
  });

  it('takes the rule and the class away again on teardown', () => {
    const down = src.slice(src.indexOf('function teardown'));
    expect(down).toContain('classList.remove(OVER_TRUCK_CLASS)');
    expect(down).toContain('getElementById(CURSOR_STYLE_ID)?.remove()');
  });
});
