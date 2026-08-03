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
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';

globalThis.ResizeObserver = class {
  observe() {} unobserve() {} disconnect() {}
} as unknown as typeof ResizeObserver;

import {
  ScrollbarH, ScrollbarV, useWheelToHorizontal, V_BAR_GUTTER,
} from './scrollbars';

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

function Bridge({ el }: { el: HTMLElement }) {
  useWheelToHorizontal(el);
  return null;
}

const wheel = (el: HTMLElement, init: WheelEventInit) =>
  act(() => {
    el.dispatchEvent(new WheelEvent('wheel', { bubbles: true, ...init }));
  });

afterEach(cleanup);

describe('the wheel bridge is installed exactly once', () => {
  it('moves by the delta ONCE with both scrollbars mounted', async () => {
    const el = makeScroller();
    render(
      <>
        <Bridge el={el} />
        <ScrollbarH el={el} />
        <ScrollbarV el={el} />
      </>,
    );
    await wheel(el, { deltaX: 30 });
    // 30, never 60.  Two bars must not mean two handlers.
    expect(el.scrollLeft).toBe(30);
  });

  it('the scrollbars themselves handle no wheel at all', async () => {
    // Without the bridge nothing moves — proving the handler lives in
    // one named place rather than riding along inside the bars.
    const el = makeScroller();
    render(
      <>
        <ScrollbarH el={el} />
        <ScrollbarV el={el} />
      </>,
    );
    await wheel(el, { deltaX: 30 });
    expect(el.scrollLeft).toBe(0);
  });

  it('stays single-handed when only one bar is mounted', async () => {
    const el = makeScroller();
    render(<><Bridge el={el} /><ScrollbarH el={el} /></>);
    await wheel(el, { deltaX: 10 });
    expect(el.scrollLeft).toBe(10);
  });
});

describe('wheel deltas are normalised to pixels', () => {
  it('treats deltaMode=0 as pixels', async () => {
    const el = makeScroller();
    render(<Bridge el={el} />);
    await wheel(el, { deltaX: 12, deltaMode: 0 });
    expect(el.scrollLeft).toBe(12);
  });

  it('scales deltaMode=1 (Firefox mouse wheel reports LINES)', async () => {
    const el = makeScroller();
    render(<Bridge el={el} />);
    await wheel(el, { deltaX: 3, deltaMode: 1 });
    // 3 lines x 16px — not a 3px nudge.
    expect(el.scrollLeft).toBe(48);
  });

  it('scales deltaMode=2 by a viewport width', async () => {
    const el = makeScroller();
    render(<Bridge el={el} />);
    await wheel(el, { deltaX: 1, deltaMode: 2 });
    expect(el.scrollLeft).toBe(100);
  });
});

describe('shift+wheel drives the horizontal axis', () => {
  it('uses deltaY when shift is held and deltaX is empty', async () => {
    const el = makeScroller();
    render(<Bridge el={el} />);
    await wheel(el, { deltaY: 25, shiftKey: true });
    expect(el.scrollLeft).toBe(25);
  });

  it('leaves a plain vertical wheel alone', async () => {
    const el = makeScroller();
    render(<Bridge el={el} />);
    await wheel(el, { deltaY: 25 });
    expect(el.scrollLeft).toBe(0);
  });
});

describe('the handler is removed with the component', () => {
  it('stops moving the container after unmount', async () => {
    const el = makeScroller();
    const view = render(<Bridge el={el} />);
    await wheel(el, { deltaX: 5 });
    expect(el.scrollLeft).toBe(5);
    view.unmount();
    await wheel(el, { deltaX: 5 });
    expect(el.scrollLeft).toBe(5);
  });
});

describe('the thumb drag uses pointer capture', () => {
  it('captures the pointer so a drag survives leaving the window', async () => {
    // Without capture the move/up listeners sat on `window` with no
    // pointercancel branch and no unmount cleanup, so a pointerup the
    // window never saw left a live handler scrolling the grid forever.
    const el = makeScroller();
    render(<><Bridge el={el} /><ScrollbarH el={el} /></>);
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
    // touch-none: on a touchscreen this bar is the ONLY horizontal
    // scrolling affordance, so the browser must not steal the gesture.
    const el = makeScroller();
    render(<><Bridge el={el} /><ScrollbarH el={el} /></>);
    const thumb = document.querySelector('[style*="width"]') as HTMLElement;
    expect(thumb.className).toContain('touch-none');
  });
});

describe('the vertical bar has a lane of its own', () => {
  // The bar is an absolute overlay, so without a reserved gutter it is
  // painted over whatever is at the scrollport's right edge — on a
  // table wide enough to need a bar, a column of real values.  Loads
  // showed it: the Source column clipped by the viewport, with a
  // scrollbar down the middle of what was left.
  it('is narrower than the space a caller must reserve for it', () => {
    const el = makeScroller();
    const { container } = render(<ScrollbarV el={el} />);
    const bar = container.firstElementChild as HTMLElement;
    // w-2 (8px) at right-0.5 (2px) — the reservation must cover both,
    // or the bar hangs back over the content it was moved off.
    expect(bar.className).toContain('w-2');
    expect(bar.className).toContain('right-0.5');
    expect(V_BAR_GUTTER).toBeGreaterThanOrEqual(8 + 2);
  });

  // An OVERLAY horizontal bar is placed against the wrapper's padding
  // box, which still contains the gutter — so it has to shift left by
  // it to end where the scrollport ends, or the two bars cross.
  it('shifts an overlay horizontal bar clear of the reserved lane', () => {
    const el = makeScroller();
    const { container } = render(
      <ScrollbarH el={el} insetRight={20} gutterRight={V_BAR_GUTTER} />,
    );
    const bar = container.firstElementChild as HTMLElement;
    expect(bar.style.right).toBe(`${20 + V_BAR_GUTTER}px`);
  });

  // A flow bar is already inside the padded content box.  Shifting it
  // again would inset it twice and leave it short of the scrollport.
  it('leaves a flow horizontal bar alone — it is already inside the padding', () => {
    const el = makeScroller();
    const { container } = render(
      <ScrollbarH el={el} insetRight={20} gutterRight={V_BAR_GUTTER} flow />,
    );
    const bar = container.firstElementChild as HTMLElement;
    expect(bar.style.marginRight).toBe('20px');
    expect(bar.style.right).toBe('');
  });
});
