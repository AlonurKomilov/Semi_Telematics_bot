/**
 * WHERE A SURFACE ENDS — held to the two facts that made it a service.
 *
 * Both came from the owner reading the rendered app rather than from
 * anything the code said, and both are the reason this is measured
 * instead of declared:
 *
 *   · "A lens only acts at an EDGE, so where two parts join it should
 *     be continuous." The rail and the header meet, and the join was
 *     bending.
 *   · "The top bar's edge is the one facing DOWN — above it is already
 *     the outside, it does not need an edge there."
 *
 * And the third, which is the one that settles measured-versus-declared
 * on its own: a side is rarely all one thing, and the first attempt
 * here used one boolean per side and came out exactly backwards.
 */
import { describe, it, expect } from 'vitest';
import {
  spansFor, readEdges, publishEdges, EDGE_VARS, SEAM, SURFACES, type OpenSpan,
} from './edges';

const VIEW = { innerWidth: 1400, innerHeight: 900 };
const box = (left: number, top: number, w: number, h: number): DOMRect => ({
  left, top, right: left + w, bottom: top + h, width: w, height: h, x: left, y: top,
  toJSON: () => ({}),
} as DOMRect);
const sideOf = (spans: OpenSpan[], side: OpenSpan['side']) => spans.filter((s) => s.side === side);

describe('where a pane actually ends', () => {
  it('closes only the stretch a neighbour actually covers', () => {
    // THE BUG THE OWNER FOUND, in numbers. The rail runs the height of
    // the window and the header sits against the top 48px of its right
    // side; the rest of that side faces the page. Read as a boolean,
    // one flush neighbour sealed the whole side — so the rail bent
    // along its OUTER edge and stayed flat along the one facing the
    // page. Exactly backwards.
    const rail = box(0, 0, 224, 800);
    const header = box(224, 0, 1176, 48);
    const right = sideOf(spansFor(rail, [header], VIEW), 'right');
    expect(right.length, 'the whole side was sealed by a 48px neighbour').toBe(1);
    // A FRACTION of the side, not pixels — see `OpenSpan`. The header
    // covers 48 of the rail's 800, so the glass starts at 0.06 of the
    // way down and runs to the end.
    expect(right[0].from, 'the open stretch starts where the header ends').toBeCloseTo(48 / 800, 3);
    expect(right[0].to, 'the open stretch runs to the bottom').toBeCloseTo(1, 6);
  });

  it('and drops a side that lies on the window itself', () => {
    const header = box(224, 0, 1176, 48);
    const spans = spansFor(header, [], VIEW);
    expect(sideOf(spans, 'top'), 'it bends against the window edge').toEqual([]);
    expect(sideOf(spans, 'right'), 'it bends against the window edge sideways').toEqual([]);
    expect(sideOf(spans, 'bottom').length, 'it stopped ending toward the page').toBe(1);
  });

  it('and leaves a side open when the neighbour is merely near', () => {
    // The control, and why the tolerance is a fraction of a pixel
    // rather than a design value: two cards with a gap between them are
    // two panes, and both keep the side that faces the other.
    const card = box(100, 100, 300, 200);
    const nextTo = box(100 + 300 + 12, 100, 300, 200);
    expect(sideOf(spansFor(card, [nextTo], VIEW), 'right').length, 'a real gap read as a seam')
      .toBe(1);
    expect(SEAM, 'the tolerance grew into a design value').toBeLessThan(4);
  });

  it('and a pane boxed in on every side ends nowhere at all', () => {
    // The centre gutter with the assistant open: page on one side,
    // sub-page on the other, window above and below. An EMPTY list, and
    // that is a measurement — not the same answer as "could not tell".
    const gutter = box(700, 0, 8, 900);
    const page = box(0, 0, 700, 900);
    const sub = box(708, 0, 692, 900);
    expect(spansFor(gutter, [page, sub], VIEW), 'a fully enclosed strip still ends somewhere')
      .toEqual([]);
  });

  it('and a neighbour that only touches at a corner seals nothing', () => {
    // Flush on x, nowhere near on y. Touching at a point is not a seam.
    const card = box(100, 100, 300, 200);
    const below = box(400, 800, 300, 100);
    expect(sideOf(spansFor(card, [below], VIEW), 'right').length, 'a distant pane sealed a side')
      .toBe(1);
  });
});

describe('one reading, shared', () => {
  const mount = (html: string) => { document.body.innerHTML = html; };

  it('answers for every surface it found', () => {
    mount('<div class="surface" id="a"></div><div class="surface" id="b"></div>');
    const reading = readEdges(document, VIEW);
    for (const id of ['a', 'b'])
      expect(reading.of(document.getElementById(id)!), `${id} was not read`).not.toBeNull();
  });

  it('and says "could not tell" rather than "nothing" for an unplaced pane', () => {
    // The distinction the service exists to keep. An empty list means
    // measured and enclosed; `undefined` means the question could not
    // be asked. Reading zeroes as seams would strip the treatment off
    // everything — which is what jsdom, with no layout at all, would
    // otherwise do to every surface in the app.
    mount('<div class="surface" id="a"></div>');
    expect(readEdges(document, VIEW).of(document.getElementById('a')!)).toBeUndefined();
  });

  it('and nothing about an element it never saw', () => {
    mount('<div class="surface"></div><div id="outside"></div>');
    expect(readEdges(document, VIEW).of(document.getElementById('outside')!)).toBeUndefined();
  });

  it('and it looks for surfaces by the one spelling', () => {
    // Two spellings of "what has an edge" is how a second axis starts
    // answering a slightly different question from the first.
    expect(SURFACES).toBe('.surface');
  });
});

describe('a surface carries its own answer', () => {
  const el = () => {
    document.body.innerHTML = '<div class="surface"></div>';
    return document.querySelector<HTMLElement>('.surface')!;
  };
  const flags = (e: HTMLElement) => Object.fromEntries(
    Object.entries(EDGE_VARS).map(([side, prop]) => [side, e.style.getPropertyValue(prop)]));

  it('marks an open side 1 and a seam 0', () => {
    // A NUMBER, not a boolean, and that is the whole design: CSS cannot
    // branch, but it can multiply. A rim's alpha is
    // `calc(0.10 * var(--surface-edge-left, 1))`, so a seam zeroes it
    // without any rule knowing what a seam is.
    const e = el();
    publishEdges(e, [{ side: 'left', from: 0, to: 1 }, { side: 'top', from: 0, to: 0.5 }]);
    expect(flags(e)).toEqual({ left: '1', top: '1', right: '0', bottom: '0' });
  });

  it('and a side draws whole when any stretch of it is open', () => {
    // The honest simplification, stated where it can be checked. An
    // inset shadow runs a whole side and cannot be interrupted, so the
    // rail — open for 94% of its right side and covered by the header
    // for the top 6% — draws for 100%. The error is a hairline behind a
    // pane that is already there. A consumer that CAN follow a partial
    // side reads the spans instead.
    const e = el();
    publishEdges(e, [{ side: 'right', from: 0.06, to: 1 }]);
    expect(flags(e).right).toBe('1');
  });

  it('and an enclosed pane is marked on no side at all', () => {
    const e = el();
    publishEdges(e, []);
    expect(flags(e)).toEqual({ top: '0', right: '0', bottom: '0', left: '0' });
  });

  it('and an unmeasured pane says nothing, so the fallback draws it', () => {
    // The distinction `readEdges` keeps, carried through to the CSS.
    // `undefined` is "could not ask" — jsdom, a pane not laid out yet,
    // the first frame. Writing zeroes there would silently strip the
    // rim off every surface in the app; REMOVING the property lets
    // `var(--surface-edge-top, 1)` fall back to drawing, which is what
    // a surface did before any of this existed.
    const e = el();
    publishEdges(e, [{ side: 'top', from: 0, to: 1 }]);
    publishEdges(e, undefined);
    expect(flags(e)).toEqual({ top: '', right: '', bottom: '', left: '' });
  });

  it('and names the four properties the material spends', () => {
    // Pinned because the writer and glass.css are the two halves of one
    // contract and neither imports the other. A rename on this side is
    // silent: the rim keeps multiplying by a fallback of 1 and every
    // seam draws again.
    expect(EDGE_VARS).toEqual({
      top: '--surface-edge-top', right: '--surface-edge-right',
      bottom: '--surface-edge-bottom', left: '--surface-edge-left',
    });
  });
});
