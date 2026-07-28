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

globalThis.ResizeObserver = class {
  observe() {}
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
  const { TABLE_PARTS } = await vi.importActual<
    typeof import('../../preferences/registry')
  >('../../preferences/registry');
  return {
    useSyncLoaded: () => true,
    useTablePreference: (_t: unknown, key: string, fallback?: unknown) => ({
      value: fallback !== undefined
        ? fallback
        : (TABLE_PARTS as Record<string, { default: unknown }>)[key]?.default,
      setValue: () => {},
    }),
    usePreference: (_k: string, fallback: unknown) => ({
      value: fallback, setValue: () => {},
    }),
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
