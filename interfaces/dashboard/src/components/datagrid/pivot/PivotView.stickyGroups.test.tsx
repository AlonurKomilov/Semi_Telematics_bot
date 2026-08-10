/**
 * Sticky column-group labels.
 *
 * A group band is as wide as everything under it — one YEAR spanning
 * sixty driver columns is several screens across. Without stickiness the
 * label saying WHICH year scrolls away long before its numbers do, and
 * the reader is left with a screen of figures belonging to nothing.
 *
 * The mechanic is CSS: the sticky span's containing block is its own
 * <th>, so it travels with the scroll and stops at its group's right
 * edge without trespassing into the next one. What a test can hold is
 * the part that silently regresses — that group labels are sticky, that
 * LEAF labels are not (they are one column wide; sticking them would
 * move text inside a cell for no reason), and that the offset clears the
 * frozen row-label column instead of sliding beneath it.
 */
import { describe, it, expect, vi, beforeAll } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { AnyColumn } from '../../../types';

beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

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
  { key: 'customer', label: 'Customer', pivotable: true },
  { key: 'region', label: 'Region', pivotable: true },
  { key: 'driver', label: 'Driver', pivotable: true },
  { key: 'rate', label: 'Rate', aggregable: true },
];

const ROWS = Array.from({ length: 12 }, (_, i) => ({
  customer: `Customer ${i % 3}`,
  region: i < 6 ? 'North' : 'South',
  driver: `Driver ${i % 4}`,
  rate: (i + 1) * 10,
}));

/** Two column levels: Region is a GROUP band, Driver is under it. */
const MODEL: PivotModel = {
  rows: ['customer'],
  columns: ['region', 'driver'],
  values: [{ key: 'rate', aggFn: 'sum' }],
};

function labelSpan(text: string): HTMLElement | null {
  const th = screen.getAllByText(text).find((el) => el.closest('th'));
  return th ?? null;
}

describe('sticky column-group labels', () => {
  it('sticks a group band label so it survives its own width', () => {
    render(<PivotView rows={ROWS} model={MODEL} columns={COLUMNS} padding="py-3" fill />);
    const north = labelSpan('North');
    expect(north).not.toBeNull();
    expect(north!.className).toContain('sticky');
    // inline-block, because `position: sticky` does nothing to a plain
    // inline box — dropping it would silently disable the whole feature
    // while leaving the class list looking right.
    expect(north!.className).toContain('inline-block');
    expect(north!.tagName).toBe('SPAN');
  });

  it('carries a left offset rather than pinning at the edge', () => {
    render(<PivotView rows={ROWS} model={MODEL} columns={COLUMNS} padding="py-3" fill />);
    const north = labelSpan('North')!;
    // ⚠️ jsdom has NO layout, so the pinned column measures 0 and the
    // offset here is only the constant gap.  The pin-clearing half of
    // `insets.left + 8` is therefore NOT proven by this test — it is
    // asserted as far as a layout-less DOM allows, and the reason the
    // term exists is written at the call site.
    expect(Number.parseInt(north.style.left, 10)).toBe(8);
  });

  it('leaves LEAF headers alone', () => {
    render(<PivotView rows={ROWS} model={MODEL} columns={COLUMNS} padding="py-3" fill />);
    // A measure header is one column wide — there is nothing to outlive,
    // and sticking it would slide text around inside a single cell.
    const leaf = screen.getAllByText('Rate').find((el) => el.closest('th'));
    expect(leaf).toBeDefined();
    expect(leaf!.className).not.toContain('sticky');
  });
});
