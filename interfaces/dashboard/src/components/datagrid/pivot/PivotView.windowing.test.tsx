/**
 * Row windowing, asserted against the real DOM.
 *
 * `pivot.test.ts` pins the ARITHMETIC (spacers + slice account for every
 * row, clamping holds at both ends).  This file pins the thing arithmetic
 * can't: that the renderer actually emits a bounded number of rows, and
 * that the pieces windowing depends on are present.
 *
 * jsdom has no layout, so `offsetHeight` / `clientHeight` are 0 — and
 * with `rowH === 0` windowing correctly disables itself, which would make
 * every assertion here vacuously pass.  So the two metrics the window is
 * computed from are stubbed: one row is 32px, the viewport is 480px.
 * Everything else is the real component.
 */
import { describe, it, expect, vi, afterEach, beforeAll, afterAll } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import type { AnyColumn } from '../../../types';

globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) =>
      (typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue) ?? k,
  }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));
vi.mock('../../../hooks/useTimezone', () => ({ useTimezone: () => 'UTC' }));

import PivotView from './PivotView';
import type { PivotModel } from './pivot';

const ROW_H = 32;
const VIEWPORT = 480;
/** Mirrors the component's own constants. */
const PER_VIEW = Math.ceil(VIEWPORT / ROW_H);   // 15
const OVERSCAN = 15;

const COLUMNS: AnyColumn[] = [
  { key: 'customer', label: 'Customer', pivotable: true },
  { key: 'driver', label: 'Driver', pivotable: true },
  { key: 'rate', label: 'Rate', aggregable: true },
];

/** 360 distinct customers across 6 drivers — the shape that hurt. */
const ROWS = Array.from({ length: 360 }, (_, i) => ({
  customer: `Customer ${String(i).padStart(3, '0')}`,
  driver: `Driver ${i % 6}`,
  rate: (i + 1) * 10,
}));

const MODEL: PivotModel = {
  rows: ['customer'], columns: ['driver'],
  values: [{ key: 'rate', aggFn: 'sum' }],
};

let descriptors: { h?: PropertyDescriptor; c?: PropertyDescriptor } = {};

beforeAll(() => {
  descriptors = {
    h: Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight'),
    c: Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight'),
  };
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
    configurable: true,
    get(this: HTMLElement) {
      // Only a real data row reports a height — that is precisely what
      // the component measures to derive the window.
      return this.matches?.('tr[data-prow]') ? ROW_H : 0;
    },
  });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get(this: HTMLElement) {
      return this.getAttribute?.('role') === 'region' ? VIEWPORT : 0;
    },
  });
});

afterAll(() => {
  if (descriptors.h) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', descriptors.h);
  if (descriptors.c) Object.defineProperty(HTMLElement.prototype, 'clientHeight', descriptors.c);
});

afterEach(cleanup);

const dataRows = () => document.querySelectorAll('tbody tr[data-prow]').length;

describe('row windowing bounds the DOM', () => {
  it('renders a window, not 360 rows', async () => {
    await act(async () => {
      render(<PivotView rows={ROWS} model={MODEL} columns={COLUMNS} padding="py-3" fill />);
    });
    // The first paint has no measured row height, so every row renders and
    // one gets measured; the window engages on the next commit.
    expect(dataRows()).toBeGreaterThan(0);
    expect(dataRows()).toBeLessThanOrEqual(PER_VIEW + OVERSCAN * 2);
    // ...and it is genuinely a fraction of the report.
    expect(dataRows()).toBeLessThan(360);
  });

  it('keeps the whole report scrollable via spacer rows', async () => {
    await act(async () => {
      render(<PivotView rows={ROWS} model={MODEL} columns={COLUMNS} padding="py-3" fill />);
    });
    // Two spacers (or one, at the top of the list) carry the rows that
    // aren't rendered — without them the scrollbar would describe only
    // the window and the report would look 40 rows long.
    const spacers = document.querySelectorAll('tbody tr[aria-hidden]');
    expect(spacers.length).toBeGreaterThan(0);
    const total = Array.from(spacers)
      .reduce((sum, el) => sum + Number.parseInt((el as HTMLElement).style.height || '0', 10), 0);
    expect(total).toBeGreaterThan(0);
  });

  it('mounts the sizer row so column widths come from the whole report', async () => {
    await act(async () => {
      render(<PivotView rows={ROWS} model={MODEL} columns={COLUMNS} padding="py-3" fill />);
    });
    // It is aria-hidden and carries no data-prow, so it can be neither
    // read out nor mistaken for a data row by the height measurement.
    const sizer = document.querySelector('tbody tr[aria-hidden]:not([style*="height"])');
    expect(sizer).not.toBeNull();
    expect(sizer!.querySelector('[data-prow]')).toBeNull();
  });

  it('does NOT window a report that fits, so there are no spacers to get wrong', async () => {
    const few = ROWS.slice(0, 8);
    await act(async () => {
      render(<PivotView rows={few} model={MODEL} columns={COLUMNS} padding="py-3" fill />);
    });
    expect(dataRows()).toBe(8);
    const sized = document.querySelectorAll('tbody tr[aria-hidden][style*="height"]');
    expect(sized.length).toBe(0);
  });

  it('does NOT window without fill — there is no owned scroller to window against', async () => {
    await act(async () => {
      render(<PivotView rows={ROWS} model={MODEL} columns={COLUMNS} padding="py-3" />);
    });
    expect(dataRows()).toBe(360);
  });

  it('exposes the matrix as a focusable, named scroll region', async () => {
    await act(async () => {
      render(<PivotView rows={ROWS} model={MODEL} columns={COLUMNS} padding="py-3" fill />);
    });
    const region = screen.getByRole('region', { name: 'Pivot report' });
    // Without tabIndex, arrows and PageDown never reach the rows and
    // everything past the first screen is mouse-only (WCAG 2.1.1).
    expect(region.getAttribute('tabindex')).toBe('0');
  });
});

describe('the frozen columns stay frozen', () => {
  // Pinning is opt-IN now, so these must ask for it explicitly — which is
  // the sharper test anyway: it exercises the pinned path rather than
  // relying on a default that can be flipped by an owner decision.
  const PINNED: PivotModel = { ...MODEL, pinRowLabels: true, pinTotals: true };

  // `sticky` and `relative` are the SAME tailwind-merge group (position),
  // so any class list that appends `relative` to a sticky cell silently
  // replaces it and the column stops freezing. That happened once, when a
  // `relative` was added to host the zebra overlay — and it is invisible
  // in jsdom (no layout) and easy to miss in a browser, because the header
  // corner keeps its own sticky and looks correct while the body labels
  // scroll away beneath it. The merged class string is checkable, so it is
  // the guard.
  const freeze = (sel: string) => {
    const el = document.querySelector(sel);
    expect(el, `expected ${sel} to exist`).not.toBeNull();
    return (el as HTMLElement).className;
  };

  it('keeps position:sticky on the row-label column', async () => {
    await act(async () => {
      render(<PivotView rows={ROWS} model={PINNED} columns={COLUMNS} padding="py-3" fill />);
    });
    const cls = freeze('tbody tr[data-prow] th');
    expect(cls).toContain('sticky');
    // The overlay's containing block comes from `sticky` itself.
    expect(cls.split(/\s+/)).not.toContain('relative');
  });

  it('keeps position:sticky on the Total column', async () => {
    await act(async () => {
      render(<PivotView rows={ROWS} model={PINNED} columns={COLUMNS} padding="py-3" fill />);
    });
    const cells = document.querySelectorAll('tbody tr[data-prow] td');
    const last = cells[cells.length - 1] as HTMLElement;
    expect(last.className).toContain('sticky');
    expect(last.className.split(/\s+/)).not.toContain('relative');
  });

  it('keeps the header corner and the totals label frozen too', async () => {
    await act(async () => {
      render(<PivotView rows={ROWS} model={PINNED} columns={COLUMNS} padding="py-3" fill />);
    });
    expect(freeze('thead th[data-pin="left"]')).toContain('sticky');
    expect(freeze('tfoot th')).toContain('sticky');
  });
});

describe('unpinned means ORDINARY, not just un-shadowed', () => {
  it('drops position, fill and data-pin when the pins are off', async () => {
    await act(async () => {
      render(<PivotView rows={ROWS} model={MODEL} columns={COLUMNS} padding="py-3" fill />);
    });
    const label = document.querySelector('tbody tr[data-prow] th') as HTMLElement;
    const cls = label.className.split(/\s+/);
    expect(cls).not.toContain('sticky');
    // ``bg-card`` exists only so a FROZEN cell can occlude what scrolls
    // under it; left on an ordinary cell it would hide the row's zebra.
    expect(cls).not.toContain('bg-card');
    // The scrollbar insets are driven off data-pin, so it has to go too or
    // the bar reserves space for an edge that now scrolls.
    expect(document.querySelector('thead th[data-pin="left"]')).toBeNull();
    expect(document.querySelector('thead th[data-pin="right"]')).toBeNull();
  });
});
