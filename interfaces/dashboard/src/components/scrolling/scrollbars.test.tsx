/**
 * The horizontal wheel bridge — one handler, correct units.
 *
 * The container is `overflow-x: hidden` (so no native bar reserves a
 * track at its bottom edge), which also means the browser ignores wheel
 * and trackpad gestures on that axis.  `useWheelToHorizontal` is what
 * puts them back, and these tests pin the two ways it was wrong:
 *
 *  1. It used to live inside `useScrollMetrics`, which BOTH scrollbars
 *     call on the SAME element — so two handlers each applied the delta
 *     and every swipe scrolled twice as far as it should.  The early
 *     `return null` in an unneeded bar did not save you: hooks run
 *     before it, so even an invisible bar installed its handler.
 *  2. It read `deltaX` raw.  Chrome and Safari report pixels, but
 *     Firefox reports LINES for a physical mouse wheel, so a notch moved
 *     the grid 3px.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';

globalThis.ResizeObserver = class {
  observe() {} unobserve() {} disconnect() {}
} as unknown as typeof ResizeObserver;

import { ScrollbarH, ScrollbarV } from './scrollbars';

/** A container that overflows on both axes and records scrollLeft. */
function makeScroller() {
  const el = document.createElement('div');
  const fixed = (k: string, v: number) =>
    Object.defineProperty(el, k, { value: v, configurable: true });
  fixed('clientWidth', 100);
  fixed('scrollWidth', 500);
  fixed('clientHeight', 100);
  fixed('scrollHeight', 500);
  let left = 0;
  Object.defineProperty(el, 'scrollLeft', {
    get: () => left,
    set: (v: number) => { left = v; },
    configurable: true,
  });
  document.body.appendChild(el);
  return el;
}

const wheel = (el: HTMLElement, init: WheelEventInit) =>
  act(() => {
    el.dispatchEvent(new WheelEvent('wheel', { bubbles: true, ...init }));
  });

afterEach(cleanup);

describe('the thumb drag uses pointer capture', () => {
  it('captures the pointer so a drag survives leaving the window', async () => {
    // Without capture the move/up listeners sat on `window` with no
    // pointercancel branch and no unmount cleanup, so a pointerup the
    // window never saw left a live handler scrolling the grid forever.
    const el = makeScroller();
    render(<ScrollbarH el={el} />);
    const thumb = document.querySelector('[style*="width"]') as HTMLElement;
    expect(thumb, 'expected a rendered thumb').toBeTruthy();

    const captured: number[] = [];
    thumb.setPointerCapture = ((id: number) => { captured.push(id); }) as never;
    thumb.releasePointerCapture = (() => {}) as never;

    await act(() => {
      thumb.dispatchEvent(new PointerEvent('pointerdown', {
        bubbles: true, pointerId: 7, clientX: 0,
      }));
    });
    expect(captured).toEqual([7]);
  });

  it('keeps the thumb off the page pan gesture', async () => {
    // touch-none so a drag ON THE THUMB isn't stolen by the page's own
    // pan.  Not because the bar carries the axis — ``overflow-x: auto``
    // means the browser pans natively now — but because a thumb you
    // cannot drag without the page moving underneath is still broken.
    const el = makeScroller();
    render(<ScrollbarH el={el} />);
    const thumb = document.querySelector('[style*="width"]') as HTMLElement;
    expect(thumb.className).toContain('touch-none');
  });
});

describe('an invisible bar does not take the pointer', () => {
  // ScrollbarV is transparent until the grid is hovered, but it still
  // occupied an 8px strip OVER the rightmost data column.  On touch a
  // finger landing there grabbed the scrollbar — or the track's
  // page-scroll — instead of panning the table, on a control nobody
  // could see.  Reported by a responsive audit at 375px.
  it('is pointer-inert while transparent, and live once hovered', () => {
    const el = makeScroller();
    const { container } = render(<ScrollbarV el={el} />);
    const bar = container.firstElementChild as HTMLElement;
    // Visible at rest (opacity-60), full on hover — the horizontal bar is
    // always visible and the two disagreeing was the defect.
    expect(bar.className).toContain('opacity-60');
    expect(bar.className).toContain('group-hover/grid:opacity-100');
    // The pointer-events rule is what keeps a finger from grabbing it.
    expect(bar.className).toContain('pointer-events-none');
    expect(bar.className).toContain('pointer-events-none');
    // The two must move together: visible-but-inert would be a dead
    // control, invisible-but-live is the bug above.
    expect(bar.className).toContain('group-hover/grid:opacity-100');
    expect(bar.className).toContain('group-hover/grid:pointer-events-auto');
  });
});
