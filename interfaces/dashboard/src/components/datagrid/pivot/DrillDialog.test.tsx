/**
 * Drill-down, at the DOM level — the toggle and the dialog.
 *
 * `drill.test.ts` pins WHICH ROWS are behind a cell.  This file pins the
 * two things that are only true in a rendered tree: that switching the
 * feature off leaves no trace of it in the matrix, and that switching it
 * on produces a dialog carrying the right rows.
 */
import { describe, it, expect, vi, afterEach, beforeAll } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import type { AnyColumn } from '../../../types';

globalThis.ResizeObserver = class {
  observe() {} unobserve() {} disconnect() {}
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

const COLUMNS: AnyColumn[] = [
  { key: 'company', label: 'Company', pivotable: true },
  { key: 'driver', label: 'Driver', pivotable: true },
  { key: 'rate', label: 'Rate', aggregable: true },
];
const ROWS = [
  { id: 1, company: 'PTG', driver: 'Ann', rate: 100 },
  { id: 2, company: 'PTG', driver: 'Ann', rate: 300 },
  { id: 3, company: 'CFT', driver: 'Bo', rate: 50 },
];
const BASE: PivotModel = {
  rows: ['company'], columns: ['driver'],
  values: [{ key: 'rate', aggFn: 'sum' }],
};

beforeAll(() => {
  // jsdom has no layout; nothing here depends on windowing, but the view
  // measures on mount and a missing method would throw.
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
    configurable: true, get() { return 0; },
  });
});

afterEach(cleanup);

const figures = () => document.querySelectorAll('tbody tr[data-prow] td button');
/** Pick a figure by what it SAYS.  Row buckets sort alphabetically, so
 *  positional indexes silently mean a different cell the moment the
 *  fixture changes. */
const figure = (text: string) => {
  const hit = Array.from(figures()).find((b) => b.textContent?.trim() === text);
  expect(hit, `no drillable figure reading "${text}"`).toBeTruthy();
  return hit as HTMLButtonElement;
};

describe('drilling is opt-in', () => {
  it('renders figures as plain text when the toggle is off', async () => {
    await act(async () => {
      render(<PivotView rows={ROWS} model={BASE} columns={COLUMNS} padding="py-3" />);
    });
    // No buttons at all: a matrix that isn't drillable must not carry a
    // focus stop per cell, and on a dense report that is thousands of
    // interactive elements a keyboard user has to tab through.
    expect(figures()).toHaveLength(0);
    // The number is still there — off means "not clickable", not "gone".
    // (getAllByText: 400 is also the row total and the grand total.)
    expect(screen.getAllByText('400').length).toBeGreaterThan(0);
  });

  it('is off when the model says nothing — the default is not "on"', async () => {
    const { drillDown, ...noFlag } = { ...BASE, drillDown: true };
    void drillDown;
    await act(async () => {
      render(<PivotView rows={ROWS} model={noFlag as PivotModel} columns={COLUMNS} padding="py-3" />);
    });
    expect(figures()).toHaveLength(0);
  });

  it('makes every non-empty figure a button when the toggle is on', async () => {
    await act(async () => {
      render(
        <PivotView
          rows={ROWS} model={{ ...BASE, drillDown: true }}
          columns={COLUMNS} padding="py-3"
        />,
      );
    });
    // PTG/Ann and CFT/Bo hold numbers; the other two intersections are
    // empty and stay dashes rather than becoming empty buttons.
    expect(figures()).toHaveLength(2);
  });
});

describe('the dialog answers for the cell that opened it', () => {
  it('lists the source rows behind the figure', async () => {
    await act(async () => {
      render(
        <PivotView
          rows={ROWS} model={{ ...BASE, drillDown: true }}
          columns={COLUMNS} padding="py-3"
        />,
      );
    });
    await act(async () => { figure('400').click(); });

    const dialog = screen.getByRole('dialog');
    // Named by its full coordinates, so the dialog stands alone.
    expect(dialog.textContent).toContain('PTG');
    expect(dialog.textContent).toContain('Ann');
    // Two loads made that 400, and the count is stated in words.
    expect(dialog.textContent).toContain('2 rows');
  });

  it('says "1 row", not "1 rows"', async () => {
    await act(async () => {
      render(
        <PivotView
          rows={ROWS} model={{ ...BASE, drillDown: true }}
          columns={COLUMNS} padding="py-3"
        />,
      );
    });
    // The CFT/Bo cell has exactly one load behind it.
    await act(async () => { figure('50').click(); });
    expect(screen.getByRole('dialog').textContent).toMatch(/1 row\b/);
  });

  it('is not mounted at all until a figure is clicked', async () => {
    await act(async () => {
      render(
        <PivotView
          rows={ROWS} model={{ ...BASE, drillDown: true }}
          columns={COLUMNS} padding="py-3"
        />,
      );
    });
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
