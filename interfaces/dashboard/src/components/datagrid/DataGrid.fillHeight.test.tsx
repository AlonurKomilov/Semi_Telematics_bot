/**
 * ``fillHeight`` — the grid owns a viewport instead of growing to fit.
 *
 * Moving the scrolling from the PAGE into a div inside the card is a
 * layout change with two consequences that stay invisible until someone
 * hits them, and both are silent regressions if this file goes red:
 *
 *   1. The scroll POSITION now survives things that change what the list
 *      IS (paging, sorting, filtering, searching, switching tab).  With
 *      a sticky header nothing on screen changes shape to reveal it —
 *      you just quietly start reading page 2 from its middle.
 *   2. A plain ``overflow`` div is not focusable, so keyboard users lose
 *      the ability to scroll the rows at all; the document used to
 *      scroll for them (WCAG 2.1.1).
 *
 * jsdom does no layout, so its ``scrollTop`` is inert.  These tests
 * install a real accessor on the element and observe what the component
 * WRITES, which is precisely the behaviour under test.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import type { SortingState } from '@tanstack/react-table';
import type { AnyColumn } from '../../types';

// Counts observe() calls so a test can prove the grid re-attaches its
// measurement observers to a replacement element.
const observed: Element[] = [];
globalThis.ResizeObserver = class {
  observe(el: Element) { observed.push(el); }
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

// The i18n mock returns the KEY when a string has no default, so the
// pagination buttons are named "common.next" / "common.previous" here.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) =>
      (typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue) ?? k,
  }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));
vi.mock('../../hooks/useTimezone', () => ({ useTimezone: () => 'UTC' }));
vi.mock('../../preferences', async () => {
  const actual = await vi.importActual<
    typeof import('../../preferences/registry')
  >('../../preferences/registry');
  const { useState } = await import('react');
  return {
    useSyncLoaded: () => true,
    // STATEFUL on purpose.  A no-op setValue means pivot can never
    // actually turn on, so any test that "toggles pivot" would be
    // asserting against a grid that never changed.
    useTablePreference: (_t: unknown, key: string, fallback?: unknown) => {
      const initial = fallback !== undefined
        ? fallback
        : (actual.TABLE_PARTS as Record<string, { default: unknown }>)[key]?.default;
      const [value, setValue] = useState(initial);
      return { value, setValue };
    },
    usePreference: (_k: string, fallback: unknown) => {
      const [value, setValue] = useState(fallback);
      return { value, setValue };
    },
  };
});

import DataGrid from './DataGrid';

interface Row extends Record<string, unknown> { id: number; type: string; name: string }

// More than one page at the default size, so paging is reachable.
const ROWS: Row[] = Array.from({ length: 60 }, (_, i) => ({
  id: i + 1,
  type: i % 2 === 0 ? 'fault' : 'health',
  name: `Truck ${i + 1}`,
}));

const COLUMNS: AnyColumn[] = [
  { key: 'name', label: 'Vehicle', sortable: true },
  { key: 'type', label: 'Type', filterable: true, filterMode: 'select' },
];

const region = () => screen.getByRole('region', { name: 'Table rows' });

/** Replace jsdom's inert scrollTop with one we can read back. */
function trackScroll(el: HTMLElement, initial = 0) {
  const writes: number[] = [];
  let value = initial;
  Object.defineProperty(el, 'scrollTop', {
    get: () => value,
    set: (v: number) => { value = v; writes.push(v); },
    configurable: true,
  });
  return { writes, current: () => value };
}

afterEach(cleanup);

describe('fillHeight — the rows are a keyboard-reachable region', () => {
  it('exposes the scroll container as a focusable, named region', () => {
    render(<DataGrid columns={COLUMNS} data={ROWS} fillHeight />);
    // Without tabIndex the div cannot take focus, so PageDown / arrows
    // never reach it and everything past the first screen becomes
    // mouse-only.  Assert the attribute itself — it IS the fix.
    expect(region().getAttribute('tabindex')).toBe('0');
  });

  it('is a region on every grid, not only the fillHeight ones', () => {
    // The container also scrolls under ``stickyHeader``, and a grid that
    // grows with the page still benefits from being announceable.
    render(<DataGrid columns={COLUMNS} data={ROWS} />);
    expect(region().getAttribute('tabindex')).toBe('0');
  });
});

describe('fillHeight — scroll resets when the list changes identity', () => {
  it('returns to the top when the page changes', async () => {
    // Driven through the controlled prop rather than a Next click:
    // under vitest, tanstack's own autoResetPageIndex fires as the row
    // model recomputes and puts the index straight back to 0, so a
    // click never lands on page 2 here.  That is the harness, not the
    // grid — and this test is about the EFFECT's pageIndex dependency,
    // which the prop exercises directly and without the interference.
    const { rerender } = render(
      <DataGrid
        columns={COLUMNS} data={ROWS} fillHeight
        pageIndex={0} pageSize={25} onPaginationChange={() => {}}
      />,
    );
    const scroll = trackScroll(region(), 420);

    await act(async () => {
      rerender(
        <DataGrid
          columns={COLUMNS} data={ROWS} fillHeight
          pageIndex={1} pageSize={25} onPaginationChange={() => {}}
        />,
      );
    });

    // Landing mid-list on a fresh page is the bug: with a sticky header
    // the view looks unchanged, so the reader gets no cue at all.
    expect(scroll.current()).toBe(0);
  });

  it('returns to the top when the PAGE SIZE changes', async () => {
    // 25 -> 250 replaces every row below the fold, so the offset the
    // reader was parked at now points at different data.  This dependency
    // was missing: the effect listed pageIndex but not pageSize.
    const { rerender } = render(
      <DataGrid
        columns={COLUMNS} data={ROWS} fillHeight
        pageIndex={0} pageSize={25} onPaginationChange={() => {}}
      />,
    );
    const scroll = trackScroll(region(), 380);

    await act(async () => {
      rerender(
        <DataGrid
          columns={COLUMNS} data={ROWS} fillHeight
          pageIndex={0} pageSize={50} onPaginationChange={() => {}}
        />,
      );
    });

    expect(scroll.current()).toBe(0);
  });

  it('carries the shared scroll-region contract, not its own overflow', async () => {
    // The container's scrolling comes from components/scrolling now.
    // Asserted on inline STYLE because that is where the contract lives —
    // as classes it was clobberable by any layout class the grid added.
    render(<DataGrid columns={COLUMNS} data={ROWS} fillHeight />);
    const el = region() as HTMLElement;
    expect(el.style.overflowY).toBe('auto');
    // x is AUTO, and that is the point: ``hidden`` left the browser with
    // no horizontal scrolling mechanism at all, so touch pan and keyboard
    // were both dead and a wide table was reachable only by dragging an
    // 8px painted thumb (WCAG 2.1.1).  The native bar is removed by
    // HIDE_NATIVE_SCROLLBAR's ``display: none`` rather than by switching
    // the axis off — exactly what the VERTICAL axis has always done.
    expect(el.style.overflowX).toBe('auto');
    // fillHeight means the grid owns a viewport, so its overscroll is
    // contained rather than chaining to the page behind it.
    expect(el.style.overscrollBehavior).toBe('contain');
  });

  it('lets a grid that owns NO viewport chain its overscroll to the page', async () => {
    // Without fillHeight or stickyHeader this container has no height cap
    // — a scroll container with zero scroll range sitting inside the
    // page's own scroller.  Containing the overscroll of a box that
    // cannot scroll could only swallow a wheel the page should have got.
    render(<DataGrid columns={COLUMNS} data={ROWS} />);
    expect((region() as HTMLElement).style.overscrollBehavior).toBe('');
  });

  it('returns to the top when the sort changes', async () => {
    // Driven through the controlled prop rather than a header click, so
    // the test pins the EFFECT's dependency rather than header markup.
    const { rerender } = render(
      <DataGrid columns={COLUMNS} data={ROWS} fillHeight sorting={[]} onSortingChange={() => {}} />,
    );
    const scroll = trackScroll(region(), 300);

    const sorted: SortingState = [{ id: 'name', desc: true }];
    await act(async () => {
      rerender(
        <DataGrid
          columns={COLUMNS} data={ROWS} fillHeight
          sorting={sorted} onSortingChange={() => {}}
        />,
      );
    });

    expect(scroll.current()).toBe(0);
  });

  it('returns to the top when a column filter changes', async () => {
    const { rerender } = render(
      <DataGrid
        columns={COLUMNS} data={ROWS} fillHeight
        columnFilters={[]} onColumnFiltersChange={() => {}}
      />,
    );
    const scroll = trackScroll(region(), 250);

    await act(async () => {
      rerender(
        <DataGrid
          columns={COLUMNS} data={ROWS} fillHeight
          columnFilters={[{ id: 'type', value: ['fault'] }]}
          onColumnFiltersChange={() => {}}
        />,
      );
    });

    expect(scroll.current()).toBe(0);
  });

  it('leaves an already-top position alone instead of writing every render', async () => {
    const { rerender } = render(
      <DataGrid columns={COLUMNS} data={ROWS} fillHeight sorting={[]} onSortingChange={() => {}} />,
    );
    const scroll = trackScroll(region(), 0);

    await act(async () => {
      rerender(
        <DataGrid
          columns={COLUMNS} data={ROWS} fillHeight
          sorting={[{ id: 'name', desc: true }]} onSortingChange={() => {}}
        />,
      );
    });

    expect(scroll.writes).toEqual([]);
  });
});

describe('a table wider than the card SAYS so', () => {
  // The complaint this answers: "the last column is half hidden".  It is
  // — a wide table clips at the card's rounded edge — but a header
  // truncated to "S…" and cells cut mid-word read as a rendering fault
  // rather than as content continuing.  The fade is the affordance.
  const fade = () =>
    document.querySelector('[aria-hidden].bg-gradient-to-l');

  it('shows no fade when the table fits', () => {
    // jsdom reports 0 for every measurement, so useOverflow sees no
    // overflow — which is exactly the "it fits" case.
    render(<DataGrid columns={COLUMNS} data={ROWS} fillHeight />);
    expect(fade()).toBeNull();
  });

  // NOT TESTED HERE, and deliberately not faked into looking tested:
  // that the fade is ``aria-hidden`` and ``pointer-events-none`` (it sits
  // over the rows, so taking pointer events would swallow clicks on the
  // last column — worse than the problem it solves).  jsdom reports 0 for
  // every measurement, so ``useOverflow`` never sees overflow and the
  // element never mounts.  A test wrapping those assertions in
  // ``if (el)`` would pass by never running them, which is worse than no
  // test at all.
});

describe('measurement survives the pivot round-trip', () => {
  // Switching pivot ON unmounts the table branch entirely; switching it
  // OFF mounts a BRAND NEW element. The three measurement observers
  // (scroll metrics, header height, pinned widths) were set up in
  // effects keyed on things that don't change across that swap, so
  // afterwards they all watched a DETACHED node: metrics froze and the
  // custom scrollbars stopped rendering, leaving a wide grid with no way
  // to scroll sideways. Callback refs re-key those effects on the
  // element, so they follow whatever is live.
  it('re-observes the replacement scroll container', async () => {
    render(<DataGrid columns={COLUMNS} data={ROWS} tableId="t" pivot fillHeight />);
    const before = screen.getByRole('region', { name: 'Table rows' });
    observed.length = 0;

    const toggle = () => screen.getByRole('button', { name: 'Pivot' }).click();
    await act(async () => { toggle(); });                       // panel opens
    await act(async () => {
      screen.getByRole('switch', { name: 'Pivot the grid' }).click();   // pivot ON
    });

    // The table branch is gone while pivoting.
    expect(screen.queryByRole('region', { name: 'Table rows' })).toBeNull();

    await act(async () => {
      screen.getByRole('switch', { name: 'Pivot the grid' }).click();   // pivot OFF
    });

    const after = screen.getByRole('region', { name: 'Table rows' });
    // A genuinely new element...
    expect(after).not.toBe(before);
    expect(before.isConnected).toBe(false);
    // ...and the grid is measuring THAT one, not the corpse.
    expect(observed).toContain(after);
  });
});
