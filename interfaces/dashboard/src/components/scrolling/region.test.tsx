/**
 * The scroll-region contract.
 *
 * Each test pins one of the four things a plain `overflow-y-auto` div is
 * missing — and each of those was measured as genuinely absent across the
 * app before this module existed (2 of 57 surfaces were focusable;
 * `overscroll-behavior` appeared zero times in the codebase).
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';
import { useState } from 'react';

import { ScrollRegion, useScrollRegion } from './region';

afterEach(cleanup);

const pane = () => document.querySelector('[data-testid="pane"]') as HTMLElement;
const Region = (p: React.ComponentProps<typeof ScrollRegion>) =>
  <ScrollRegion data-testid="pane" {...p} />;

/** Records every write, so a write that happens at mount is observable —
 *  asserting a FINAL value cannot tell "never written" from "written and
 *  restored". */
function track(el: HTMLElement, initial = 0) {
  let v = initial;
  const writes: number[] = [];
  Object.defineProperty(el, 'scrollTop', {
    get: () => v, set: (n: number) => { v = n; writes.push(n); }, configurable: true,
  });
  return { writes, current: () => v };
}

describe('the four things a plain overflow div is missing', () => {
  it('is focusable, so a keyboard can scroll it at all (WCAG 2.1.1)', () => {
    render(<ScrollRegion data-testid="pane">rows</ScrollRegion>);
    expect(pane().getAttribute('tabindex')).toBe('0');
  });

  it('contains overscroll, so reaching the end does not scroll the page behind', () => {
    render(<ScrollRegion data-testid="pane">rows</ScrollRegion>);
    expect(pane().style.overscrollBehavior).toBe('contain');
  });

  it('pads the scrollport away from sticky chrome (WCAG 2.4.11)', () => {
    render(
      <ScrollRegion data-testid="pane" stickyTop={48} pinnedLeft={120} stickyBottom={36}>
        rows
      </ScrollRegion>,
    );
    const el = pane();
    expect(el.style.scrollPaddingTop).toBe('48px');
    expect(el.style.scrollPaddingLeft).toBe('120px');
    expect(el.style.scrollPaddingBottom).toBe('36px');
  });

  it('becomes a NAMED landmark only when there is a name for it', () => {
    // An unnamed landmark is worse than none — it adds an entry to the
    // screen reader's list that says nothing.
    const { rerender } = render(<ScrollRegion data-testid="pane">rows</ScrollRegion>);
    expect(pane().getAttribute('role')).toBeNull();

    rerender(<ScrollRegion data-testid="pane" label="Notifications">rows</ScrollRegion>);
    expect(pane().getAttribute('role')).toBe('region');
    expect(pane().getAttribute('aria-label')).toBe('Notifications');
  });
});

describe('the contract survives the caller', () => {
  // The contract is inline STYLE, which outranks every class — so a
  // layout class cannot delete it, and no CSS-emit-order question arises.
  it('a caller\'s overflow-hidden class cannot delete the scrolling', () => {
    render(<ScrollRegion data-testid="pane" className="overflow-hidden">rows</ScrollRegion>);
    expect(pane().style.overflowY).toBe('auto');
  });

  it('a caller cannot bypass overscroll containment by class', () => {
    // Opting out has to go through the typed option, so it is greppable.
    render(<ScrollRegion data-testid="pane" className="overscroll-auto">rows</ScrollRegion>);
    expect(pane().style.overscrollBehavior).toBe('contain');
  });

  it('a caller\'s inline style cannot delete it either', () => {
    render(
      <ScrollRegion data-testid="pane" style={{ overflowY: 'hidden' }}>rows</ScrollRegion>,
    );
    expect(pane().style.overflowY).toBe('auto');
  });

  it('still applies the caller\'s own layout classes and styles', () => {
    render(
      <ScrollRegion data-testid="pane" className="flex-1 min-h-0 px-4" style={{ maxHeight: 300 }}>
        rows
      </ScrollRegion>,
    );
    const el = pane();
    expect(el.className).toContain('flex-1');
    expect(el.className).toContain('px-4');
    expect(el.style.maxHeight).toBe('300px');
    expect(el.style.overflowY).toBe('auto');
  });
});

describe('axis is two independent axes, because CSS is', () => {
  it('scrolls y by default', () => {
    render(<ScrollRegion data-testid="pane">rows</ScrollRegion>);
    expect(pane().style.overflowY).toBe('auto');
  });

  it('honours the x and both shorthands', () => {
    const { rerender } = render(<ScrollRegion data-testid="pane" axis="x">rows</ScrollRegion>);
    expect(pane().style.overflowX).toBe('auto');
    rerender(<ScrollRegion data-testid="pane" axis="both">rows</ScrollRegion>);
    expect(pane().style.overflowY).toBe('auto');
    expect(pane().style.overflowX).toBe('auto');
  });

  it('expresses y-auto + x-hidden — the combination painted bars require', () => {
    // The three-value enum could name only 3 of 9 combinations and
    // missed this one, which is the module's OWN documented precondition
    // for useWheelToHorizontal.  Both flagship consumers need it.
    render(<ScrollRegion data-testid="pane" axis={{ y: 'auto', x: 'hidden' }}>rows</ScrollRegion>);
    expect(pane().style.overflowY).toBe('auto');
    expect(pane().style.overflowX).toBe('hidden');
  });

  it('sets nothing for an axis the caller left out', () => {
    render(<ScrollRegion data-testid="pane" axis={{ x: 'hidden' }}>rows</ScrollRegion>);
    expect(pane().style.overflowY).toBe('');
  });

  it('lets a caller opt out of containment, but only explicitly', () => {
    render(<ScrollRegion data-testid="pane" allowScrollChaining>rows</ScrollRegion>);
    expect(pane().style.overscrollBehavior).toBe('');
  });
});

describe('resetKey — position is relative to the list you were reading', () => {
  it('returns to the top when the key changes', async () => {
    const { rerender } = render(<Region resetKey="a">rows</Region>);
    const t = track(pane(), 400);
    await act(async () => { rerender(<Region resetKey="b">rows</Region>); });
    expect(t.current()).toBe(0);
  });

  it('holds position when the key is unchanged', async () => {
    const { rerender } = render(<Region resetKey="a">rows</Region>);
    const t = track(pane(), 400);
    await act(async () => { rerender(<Region resetKey="a">other children</Region>); });
    expect(t.current()).toBe(400);
  });

  it('never touches position when no key was given', async () => {
    const { rerender } = render(<Region>rows</Region>);
    const t = track(pane(), 250);
    await act(async () => { rerender(<Region>changed</Region>); });
    expect(t.current()).toBe(250);
  });

  it('writes NOTHING on mount, so a restored position survives', async () => {
    // A fresh element is already at 0, so a mount reset can only destroy
    // someone else's work — React flushes layout effects before passive
    // ones, so a parent restoring an offset would be zeroed.
    // Asserted on the WRITE LOG: a final value of 0 cannot distinguish
    // "never written" from "written".
    const el = document.createElement('div');
    document.body.appendChild(el);
    const seen: number[] = [];
    Object.defineProperty(el, 'scrollTop', {
      get: () => 0, set: (n: number) => { seen.push(n); }, configurable: true,
    });
    function Mounted() {
      const { ref, props } = useScrollRegion({ resetKey: 'a' });
      return <div ref={(n) => ref(n ?? el)} {...props} />;
    }
    await act(async () => { render(<Mounted />); });
    expect(seen).toEqual([]);
  });

  it('resets the horizontal axis too when the region owns it', async () => {
    const { rerender } = render(<Region axis="both" resetKey="a">rows</Region>);
    const el = pane();
    let left = 300;
    Object.defineProperty(el, 'scrollLeft', {
      get: () => left, set: (n: number) => { left = n; }, configurable: true,
    });
    await act(async () => { rerender(<Region axis="both" resetKey="b">rows</Region>); });
    expect(left).toBe(0);
  });
});

describe('the hook serves consumers that own their own container', () => {
  // DataGrid and PivotView compose their own classes onto their own div.
  // A wrapper they could not use would be a SECOND source of truth, not
  // a single one — so the hook is the primary export.
  function Custom() {
    const { ref, props } = useScrollRegion({
      label: 'Table rows', stickyTop: 40, axis: { y: 'auto', x: 'hidden' },
    });
    return (
      <div ref={ref} {...props} className="flex-1 min-h-0" data-testid="custom" />
    );
  }

  it('gives the caller every part of the contract to compose', () => {
    render(<Custom />);
    const el = document.querySelector('[data-testid="custom"]') as HTMLElement;
    expect(el.style.overflowY).toBe('auto');
    expect(el.style.overflowX).toBe('hidden');
    expect(el.style.overscrollBehavior).toBe('contain');
    expect(el.className).toContain('flex-1');
    expect(el.getAttribute('tabindex')).toBe('0');
    expect(el.getAttribute('aria-label')).toBe('Table rows');
    expect(el.style.scrollPaddingTop).toBe('40px');
  });

  it('republishes the node when the ELEMENT IS REPLACED', async () => {
    // This is the whole justification for callback-ref-plus-state over a
    // plain ref: the grid unmounts and remounts its scroller when pivot
    // toggles, and an effect keyed on anything else ends up observing a
    // detached node — measurement freezes and the scrollbars silently
    // stop rendering.  So the test must actually SWAP the element.
    const seen: (HTMLElement | null)[] = [];
    function Swapper() {
      const [which, setWhich] = useState(0);
      const { ref, node } = useScrollRegion();
      seen.push(node);
      return (
        <>
          <button type="button" onClick={() => setWhich(1)}>swap</button>
          {which === 0
            ? <div key="a" ref={ref} data-el="a" />
            : <div key="b" ref={ref} data-el="b" />}
        </>
      );
    }
    render(<Swapper />);
    const before = seen[seen.length - 1];
    expect(before?.getAttribute('data-el')).toBe('a');

    await act(async () => {
      (document.querySelector('button') as HTMLButtonElement).click();
    });
    const after = seen[seen.length - 1];
    expect(after?.getAttribute('data-el')).toBe('b');
    expect(after).not.toBe(before);
    expect(after?.isConnected).toBe(true);
  });
});
