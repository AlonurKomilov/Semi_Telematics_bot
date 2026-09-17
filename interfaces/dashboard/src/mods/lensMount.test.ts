/**
 * Mounting a bevel: the filter that gets built, and who is offered one.
 *
 * The claim worth guarding here is not that a filter appears. It is
 * that THIS FILE DOES NOT DECIDE WHICH SURFACES ARE GLASS — it hands
 * every surface a value and `glass.css` spends or ignores it. The
 * alternative, selecting the translucent set here, means the same
 * selector written twice in two languages, and the day they disagree
 * nothing fails: the lens simply stops reaching a surface, or reaches
 * one that is supposed to occlude.
 *
 * The encoder is injected throughout. jsdom has no 2D context, so a
 * mount that called `toDataURL` directly could only be tested in a
 * browser — which in practice means tested nowhere.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  BUCKET, LENS_VAR, bucketed, lensFilterId, ensureFilter, applyLens, radiusOf,
  installLens, openEdges, edgeKey, type Encoder,
} from './lensMount';
import type { Bevel } from './lens';

const BEVEL: Bevel = { band: 30, falloff: 2.2, strength: 13 };
/** Counts what it was asked to encode, so a test can tell a cache hit
 *  from a rebuild — which is the only externally visible difference. */
function stubEncoder(): Encoder & { calls: number } {
  const e = ((map) => { e.calls++; return `data:image/png;base64,MAP${map.width}x${map.height}`; }) as
    Encoder & { calls: number };
  e.calls = 0;
  return e;
}

beforeEach(() => { document.body.innerHTML = ''; });

describe('bucketing', () => {
  it('rounds up, never to nearest', () => {
    // Down would make the last bucket narrower than the pane it serves,
    // and the bevel would start inside the edge rather than on it.
    expect(bucketed(1)).toBe(BUCKET);
    expect(bucketed(BUCKET)).toBe(BUCKET);
    expect(bucketed(BUCKET + 1)).toBe(BUCKET * 2);
    expect(bucketed(BUCKET * 2 - 1)).toBe(BUCKET * 2);
  });

  it('never answers zero, whatever it is given', () => {
    // A filter region of zero is one the browser rejects rather than
    // skips, and 0 is what a collapsed panel measures.
    for (const px of [0, -5, 0.4]) expect(bucketed(px)).toBeGreaterThanOrEqual(BUCKET);
  });

  it('gives panes of a similar size the SAME filter', () => {
    // The point of bucketing: a list that grows a scrollbar changes its
    // pane by a few px, and minting a filter per pixel mints one per
    // interaction.
    expect(lensFilterId(bucketed(313), bucketed(300), 12))
      .toBe(lensFilterId(bucketed(320), bucketed(300), 12));
  });

  it('but not panes of a different shape', () => {
    expect(lensFilterId(240, 300, 12)).not.toBe(lensFilterId(240, 300, 28));
    expect(lensFilterId(240, 300, 12)).not.toBe(lensFilterId(480, 300, 12));
  });
});

describe('the filter that gets built', () => {
  it('lands in one host node, out of flow', () => {
    const encode = stubEncoder();
    ensureFilter(document, 240, 300, 28, BEVEL, encode);
    ensureFilter(document, 480, 300, 28, BEVEL, encode);
    const hosts = document.querySelectorAll('svg#mods-lens-filters');
    expect(hosts.length, 'more than one host, or none').toBe(1);
    expect(hosts[0].getAttribute('style')).toContain('position:absolute');
    expect(hosts[0].querySelectorAll('filter').length).toBe(2);
  });

  it('is built once per shape and then reused', () => {
    const encode = stubEncoder();
    ensureFilter(document, 240, 300, 28, BEVEL, encode);
    ensureFilter(document, 240, 300, 28, BEVEL, encode);
    ensureFilter(document, 240, 300, 28, BEVEL, encode);
    expect(encode.calls, 'the map was redrawn for a shape already mounted').toBe(1);
  });

  it('pins the region to the border box in user units', () => {
    // A percentage region grows with the element and takes the bevel
    // with it — the bug the very first attempt shipped.
    const encode = stubEncoder();
    const id = ensureFilter(document, 240, 300, 28, BEVEL, encode);
    const f = document.getElementById(id)!;
    expect(f.getAttribute('filterUnits')).toBe('userSpaceOnUse');
    expect(f.getAttribute('width')).toBe('240');
    expect(f.getAttribute('height')).toBe('300');
  });

  it('stretches one small map over the whole pane', () => {
    const encode = stubEncoder();
    const id = ensureFilter(document, 240, 300, 28, BEVEL, encode);
    const img = document.getElementById(id)!.querySelector('feImage')!;
    expect(img.getAttribute('preserveAspectRatio'), 'the map keeps its own ratio and leaves a gap')
      .toBe('none');
    expect(img.getAttribute('width')).toBe('240');
    // The map itself is small — that is the performance decision, and
    // the stub reports the size it was handed.
    expect(img.getAttribute('href')).toBe('data:image/png;base64,MAP32x40');
  });

  it('turns the pack strength into the scale the filter wants', () => {
    // A channel runs 0..1 and displaces by scale × (c − 0.5), so the
    // peak is HALF the scale. Passing the strength through unchanged
    // would halve the bevel and look like a tuning problem.
    const encode = stubEncoder();
    const id = ensureFilter(document, 240, 300, 28, BEVEL, encode);
    const d = document.getElementById(id)!.querySelector('feDisplacementMap')!;
    expect(d.getAttribute('scale')).toBe(String(BEVEL.strength * 2));
    expect(d.getAttribute('xChannelSelector')).toBe('R');
    expect(d.getAttribute('yChannelSelector')).toBe('G');
  });

  it('builds nothing when the encoder cannot', () => {
    // No canvas, no map. The surface keeps the rest of its material
    // rather than pointing at a filter that does not exist.
    const id = ensureFilter(document, 240, 300, 28, BEVEL, () => '');
    expect(id).toBe('');
    expect(document.querySelectorAll('filter').length).toBe(0);
  });
});

describe('what a surface is handed', () => {
  it('a property naming its own filter, and nothing else', () => {
    const el = document.createElement('div');
    const encode = stubEncoder();
    applyLens(el, 240, 300, 28, BEVEL, document, encode);
    // The id names the BUCKET, not the measurement — 300 rounds up to
    // 312 — which is the whole reason two panes a few pixels apart
    // share one filter.
    expect(el.style.getPropertyValue(LENS_VAR))
      .toBe(`url(#${lensFilterId(bucketed(240), bucketed(300), 28)})`);
    // NOT the filter itself: writing `backdrop-filter` here would beat
    // the escape hatch that makes floating surfaces opaque, and a menu
    // would show the page through itself again.
    expect(el.style.backdropFilter ?? '').toBe('');
  });

  it('nothing at all when the pane has no area', () => {
    const el = document.createElement('div');
    el.style.setProperty(LENS_VAR, 'url(#stale)');
    applyLens(el, 0, 0, 12, BEVEL, document, stubEncoder());
    expect(el.style.getPropertyValue(LENS_VAR), 'a collapsed pane kept a stale filter').toBe('');
  });

  it('nothing at all when the encoder failed', () => {
    const el = document.createElement('div');
    el.style.setProperty(LENS_VAR, 'url(#stale)');
    applyLens(el, 240, 300, 12, BEVEL, document, () => '');
    expect(el.style.getPropertyValue(LENS_VAR)).toBe('');
  });

  it('reads the corner off the element, not off the token', () => {
    // A call site may have overridden the radius, and the bevel has to
    // follow the corner that actually paints.
    const el = document.createElement('div');
    // The LONGHAND: jsdom does not expand the shorthand into corners,
    // and the property this reads is the one a browser resolves.
    el.style.borderTopLeftRadius = '18px';
    document.body.appendChild(el);
    expect(radiusOf(el, window)).toBe(18);
  });
});

describe('the stylesheet decides, not this file', () => {
  const CSS = readFileSync(
    join(__dirname, 'store', 'items', 'material', 'glass.css'), 'utf8',
  );
  const TS = readFileSync(join(__dirname, 'lensMount.ts'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/\/\/[^\n]*/g, (m) => m.replace(/[^\n]/g, ' '));

  it('this file names no surface category of its own', () => {
    // The whole design: offer every surface a value and let the
    // stylesheet spend it. The moment this file starts asking which
    // surfaces are popovers, the rule about what glass IS exists twice.
    for (const word of ['surface-popover', 'surface-opaque', 'chrome-pane'])
      expect(TS.includes(word), `lensMount.ts decides about ${word}`).toBe(false);
  });

  it('and the escape hatch still throws the whole filter away', () => {
    // Which is what makes the design safe: a floating surface cannot be
    // lensed even when it has been handed a filter, because `none`
    // replaces the entire backdrop-filter value.
    expect(CSS).toMatch(/backdrop-filter:\s*none/);
  });
});

/**
 * Installing and — the half that matters — uninstalling.
 *
 * Both observers are stubbed rather than shimmed: jsdom ships neither,
 * and a stub lets a test fire a resize on demand instead of hoping one
 * happens. What is being checked is the WIRING, which is the part a
 * browser would not tell you about until a surface somewhere had a
 * bevel meant for a different size.
 */
describe('installing', () => {
  type Cb = (entries: { target: Element; contentRect: { width: number; height: number };
    borderBoxSize?: { inlineSize: number; blockSize: number }[] }[]) => void;
  let fired: Cb | null = null;
  let observed: Element[] = [];
  let disconnected = 0;

  function fakeView(): Window {
    class RO {
      constructor(cb: Cb) { fired = cb; }
      observe(el: Element) { observed.push(el); }
      disconnect() { disconnected++; }
    }
    class MO {
      observe() {}
      disconnect() { disconnected++; }
    }
    return {
      ResizeObserver: RO, MutationObserver: MO,
      getComputedStyle: (el: Element) => window.getComputedStyle(el),
    } as unknown as Window;
  }

  const surface = () => {
    const el = document.createElement('div');
    el.className = 'surface';
    document.body.appendChild(el);
    return el;
  };

  beforeEach(() => { fired = null; observed = []; disconnected = 0; });

  it('watches every surface it can find', () => {
    const a = surface(), b = surface();
    installLens({ doc: document, view: fakeView(), bevel: BEVEL, encode: stubEncoder() });
    expect(observed).toContain(a);
    expect(observed).toContain(b);
  });

  it('hands each one the filter for its own measured shape', () => {
    const el = surface();
    installLens({ doc: document, view: fakeView(), bevel: BEVEL, encode: stubEncoder() });
    fired!([{ target: el, contentRect: { width: 313, height: 200 } }]);
    expect(el.style.getPropertyValue(LENS_VAR))
      .toBe(`url(#${lensFilterId(bucketed(313), bucketed(200), 0)})`);
  });

  it('prefers the border box, which is what the filter region is', () => {
    // `contentRect` excludes padding and the border; the filter lands on
    // the border box, so measuring the content box puts the bevel
    // inside the edge by however much padding the card has — which on
    // this app's cards is 16px, half a band.
    const el = surface();
    installLens({ doc: document, view: fakeView(), bevel: BEVEL, encode: stubEncoder() });
    fired!([{
      target: el,
      contentRect: { width: 100, height: 100 },
      borderBoxSize: [{ inlineSize: 313, blockSize: 200 }],
    }]);
    expect(el.style.getPropertyValue(LENS_VAR))
      .toBe(`url(#${lensFilterId(bucketed(313), bucketed(200), 0)})`);
  });

  it('does nothing at all for a material with no bevel', () => {
    // `solid` must cost exactly nothing: no observers, no filters, no
    // properties. The install still returns a teardown so the caller
    // does not have to know which case it got.
    const el = surface();
    el.style.setProperty(LENS_VAR, 'url(#stale)');
    const off = installLens({ doc: document, view: fakeView(), encode: stubEncoder() });
    expect(observed).toEqual([]);
    expect(el.style.getPropertyValue(LENS_VAR), 'a stale lens survived the switch to solid').toBe('');
    expect(typeof off).toBe('function');
    off();
  });

  it('and nothing at all where the observers do not exist', () => {
    // A server render, or a test that has not stubbed them. Returning a
    // no-op is the difference between a blank page and a crash.
    const el = surface();
    const off = installLens({
      doc: document, view: {} as Window, bevel: BEVEL, encode: stubEncoder(),
    });
    expect(el.style.getPropertyValue(LENS_VAR)).toBe('');
    expect(() => off()).not.toThrow();
  });

  it('takes the lens back off when it is torn down', () => {
    const el = surface();
    const off = installLens({ doc: document, view: fakeView(), bevel: BEVEL, encode: stubEncoder() });
    fired!([{ target: el, contentRect: { width: 240, height: 300 } }]);
    expect(el.style.getPropertyValue(LENS_VAR)).not.toBe('');
    off();
    expect(el.style.getPropertyValue(LENS_VAR), 'the surface kept a filter nobody will rebuild')
      .toBe('');
    expect(disconnected, 'an observer was left running').toBe(2);
  });
});

/**
 * WHICH SIDES ARE REAL, MEASURED AND NOT DECLARED.
 *
 * This is what lets one rule written for the frame reach all five of
 * its sides without any of them saying anything about its own
 * geometry. The engine looks at where the panes actually are.
 */
describe('a seam is where two panes meet', () => {
  const box = (left: number, top: number, w: number, h: number): DOMRect => ({
    left, top, right: left + w, bottom: top + h, width: w, height: h, x: left, y: top,
    toJSON: () => ({}),
  } as DOMRect);

  it('seals the side a flush neighbour is against', () => {
    // The rail and the header: the header's bottom sits on the rail's
    // top, so neither has an edge there.
    const rail = box(0, 48, 224, 600);
    const header = box(0, 0, 1400, 48);
    expect(openEdges(rail, [header]).top, 'the rail still bends where the header sits on it')
      .toBe(false);
    expect(openEdges(rail, [header]).right, 'the rail stopped bending toward the page')
      .toBe(true);
  });

  it('and leaves a side open when the neighbour is merely near', () => {
    // The control, and the reason the tolerance is a fraction of a
    // pixel rather than a design value: two cards with a gap between
    // them are two panes, and both keep the edge that faces the other.
    const card = box(0, 0, 300, 200);
    const nextTo = box(312, 0, 300, 200);
    expect(openEdges(card, [nextTo]).right, 'a real gap was read as a seam').toBe(true);
  });

  it('and ignores a neighbour that only shares a line, not a side', () => {
    // Flush on x but nowhere near on y — the corner of something
    // elsewhere on the page. Touching at a point is not a seam.
    const card = box(0, 0, 300, 200);
    const below = box(300, 900, 300, 200);
    expect(openEdges(card, [below]).right, 'a distant pane sealed a side').toBe(true);
  });

  it('and a sealed pane gets its own filter, not the whole one\'s', () => {
    // Two panes of the same SIZE but different seams must not share a
    // map: the id is what keeps them apart, and without the mask in it
    // the second one would silently reuse the first one's bevel.
    const whole = { top: true, right: true, bottom: true, left: true };
    const sealed = { ...whole, left: false };
    expect(edgeKey(whole)).not.toBe(edgeKey(sealed));
    expect(lensFilterId(240, 600, 0, whole)).not.toBe(lensFilterId(240, 600, 0, sealed));
  });
});
