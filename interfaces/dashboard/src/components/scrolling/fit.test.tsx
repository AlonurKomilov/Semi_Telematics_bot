/**
 * ``useFittedHeight`` — the grid finds its own viewport.
 *
 * This replaced a two-part contract (a `fillHeight` prop AND the page
 * wrapping the grid in `flex h-full flex-col min-h-0`) that 3 of 40 grid
 * surfaces had actually paid.  The whole point is that it needs nothing
 * from the page, so the tests that matter are the ones proving it FAILS
 * OPEN: every way the measurement can go wrong must land on "grow
 * naturally", which is the behaviour the other 37 pages already had.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { useState } from 'react';
import { render, cleanup, act } from '@testing-library/react';
import { useFittedHeight } from './fit';

const observers: { cb: ResizeObserverCallback; targets: Element[] }[] = [];
globalThis.ResizeObserver = class {
  targets: Element[] = [];
  constructor(public cb: ResizeObserverCallback) {
    observers.push({ cb, targets: this.targets });
  }
  observe(el: Element) { this.targets.push(el); }
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

/** Mounts a div, measures it, and reports the answer as text. */
function Fitted({ enabled = true }: { enabled?: boolean }) {
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  const h = useFittedHeight(el, enabled);
  return <div ref={setEl} data-testid="target">{h === null ? 'natural' : String(h)}</div>;
}

/** A scrolling ancestor with a known height.  jsdom resolves inline
 *  styles through getComputedStyle, and every rect is 0, so the target's
 *  offset inside the region computes as 0 and the region's whole
 *  clientHeight reads as available. */
function region(room: number, padBottom = 0) {
  const el = document.createElement('div');
  el.style.overflowY = 'auto';
  el.style.paddingBottom = `${padBottom}px`;
  Object.defineProperty(el, 'clientHeight', { value: room, configurable: true });
  document.body.appendChild(el);
  return el;
}

afterEach(() => { cleanup(); observers.length = 0; document.body.innerHTML = ''; });

describe('useFittedHeight', () => {
  it('clamps to the room left in the scrolling ancestor', () => {
    const { getByTestId } = render(<Fitted />, { container: region(600) });
    expect(getByTestId('target').textContent).toBe('600');
  });

  // The gap under the card IS the shell's own padding.  Reading it beats
  // a constant that silently disagrees the day the shell is restyled.
  it('leaves the region its own bottom padding', () => {
    const { getByTestId } = render(<Fitted />, { container: region(600, 24) });
    expect(getByTestId('target').textContent).toBe('576');
  });

  // Pages that stack charts and KPI cards above their table fall through
  // here on their own — those pages ARE taller than a screen, and page
  // scrolling is the right answer for them.
  it('gives up rather than build a viewport too small to use', () => {
    const { getByTestId } = render(<Fitted />, { container: region(200) });
    expect(getByTestId('target').textContent).toBe('natural');
  });

  // Every one of these was the behaviour of 37 pages before this
  // existed, so landing on it can never be a regression.
  it('falls back to natural height with no scrolling ancestor', () => {
    const plain = document.createElement('div');
    document.body.appendChild(plain);
    const { getByTestId } = render(<Fitted />, { container: plain });
    expect(getByTestId('target').textContent).toBe('natural');
  });

  it('falls back to natural height when opted out', () => {
    const { getByTestId } = render(<Fitted enabled={false} />, { container: region(600) });
    expect(getByTestId('target').textContent).toBe('natural');
  });

  it('falls back to natural height where ResizeObserver does not exist', () => {
    const keep = globalThis.ResizeObserver;
    // @ts-expect-error — deliberately removing it
    delete globalThis.ResizeObserver;
    try {
      const { getByTestId } = render(<Fitted />, { container: region(600) });
      expect(getByTestId('target').textContent).toBe('natural');
    } finally { globalThis.ResizeObserver = keep; }
  });

  // A footnote under a table, a second card, an activity strip — clamping
  // without subtracting them pushes that content past the fold, and a
  // region whose pointer sits over the grid scrolls the GRID, so it can
  // be close to unreachable.  Work Orders: a one-line hint landed at
  // y796-828 in an 812px viewport.
  it('leaves room for whatever is laid out AFTER the element', () => {
    const host = region(600);
    const target = document.createElement('div');
    host.appendChild(target);
    const footnote = document.createElement('p');
    Object.defineProperty(footnote, 'getBoundingClientRect', {
      value: () => ({ height: 40, top: 0, bottom: 0, left: 0, right: 0, width: 0, x: 0, y: 0, toJSON: () => {} }),
      configurable: true,
    });
    host.appendChild(footnote);
    render(<Fitted />, { container: target });
    expect(target.querySelector('[data-testid=target]')!.textContent).toBe('560');
  });

  // Siblings BELOW cannot depend on the element's height, so subtracting
  // them stays idempotent — otherwise the clamp and the re-measure would
  // chase each other.
  it('still agrees with itself when something follows', () => {
    const host = region(600);
    const target = document.createElement('div');
    host.appendChild(target);
    const after = document.createElement('p');
    Object.defineProperty(after, 'getBoundingClientRect', {
      value: () => ({ height: 40, top: 0, bottom: 0, left: 0, right: 0, width: 0, x: 0, y: 0, toJSON: () => {} }),
      configurable: true,
    });
    host.appendChild(after);
    render(<Fitted />, { container: target });
    const el = target.querySelector('[data-testid=target]')!;
    expect(el.textContent).toBe('560');
    for (const o of observers) o.cb([], {} as ResizeObserver);
    expect(el.textContent).toBe('560');
  });

  // The loop-breaker.  Clamping the card shortens the page, which fires
  // the page-root observer, which measures again — and must get the same
  // answer and write nothing, or the two feed each other forever.
  it('writes nothing when a re-measure agrees with what it already applied', async () => {
    const { getByTestId } = render(<Fitted />, { container: region(600) });
    const el = getByTestId('target');
    expect(el.textContent).toBe('600');
    // Fire every observer, then let the rAF the handler schedules run.
    await act(async () => {
      for (const o of observers) o.cb([], {} as ResizeObserver);
      await new Promise(r => setTimeout(r, 32));
    });
    expect(el.textContent).toBe('600');
  });

  // The observers must include the region's own child.  Content arriving
  // ABOVE the grid after first paint — a hero strip resolving from its
  // own query — moves the grid DOWN without resizing the region or the
  // grid's parent, so neither of those two would ever fire.
  it('watches the page root, not just the region and the parent', () => {
    const host = region(600);
    const pageRoot = document.createElement('div');
    host.appendChild(pageRoot);
    render(<Fitted />, { container: pageRoot });
    const watched = observers.flatMap(o => o.targets);
    expect(watched).toContain(host);
    expect(watched).toContain(host.firstElementChild);
  });
});
