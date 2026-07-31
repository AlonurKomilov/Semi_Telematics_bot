/**
 * A stored pivot model must survive the trip back out of preferences.
 *
 * `pivot.test.ts` pins `prunePivotModel`, but the prune is only HALF the
 * persistence path.  `DataGrid` rebuilds the model from the stored
 * preference before pruning it, and that rebuild used to list the fields
 * BY HAND — so a field added to the model and to the prune, but not to
 * the hand-written list, was silently dropped between the two.  Every
 * pivot test still passed, because they all call `pivot()` directly and
 * never touch persistence.
 *
 * That is exactly how `sort` shipped dead.  It then happened AGAIN to
 * `hideEmptyColumns`, `pinRowLabels` and `pinTotals` — three settings
 * that wrote themselves to the preference correctly and were stripped on
 * the way back, so their checkboxes did nothing at all.
 *
 * These tests observe the PANEL, which renders from the rebuilt model.
 * A field that doesn't survive shows up here as an unchecked box.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import type { AnyColumn } from '../../types';
import type { PivotModel } from './pivot/pivot';

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
vi.mock('../../hooks/useTimezone', () => ({ useTimezone: () => 'UTC' }));

/** Seeded per test — stands in for "what was saved last session". */
let stored: { enabled: boolean; model: PivotModel } | null = null;

vi.mock('../../preferences', async () => {
  const actual = await vi.importActual<
    typeof import('../../preferences/registry')
  >('../../preferences/registry');
  const { useState } = await import('react');
  return {
    useSyncLoaded: () => true,
    // STATEFUL, and every OTHER key still gets its registry default —
    // returning undefined for them crashes the grid long before it
    // reaches the pivot model this file is about.
    useTablePreference: (_t: unknown, key: string, fallback?: unknown) => {
      const fallbackFor = fallback !== undefined
        ? fallback
        : (actual.TABLE_PARTS as Record<string, { default: unknown }>)[key]?.default;
      // eslint-disable-next-line react-hooks/rules-of-hooks
      const [value, setValue] = useState(
        key === 'pivot' ? stored : fallbackFor,
      );
      return { value, setValue };
    },
    usePreference: (_k: string, fallback: unknown) => {
      // eslint-disable-next-line react-hooks/rules-of-hooks
      const [value, setValue] = useState(fallback);
      return { value, setValue };
    },
  };
});

import DataGrid from './DataGrid';

const COLUMNS: AnyColumn[] = [
  { key: 'company', label: 'Company', pivotable: true },
  { key: 'driver', label: 'Driver', pivotable: true },
  { key: 'rate', label: 'Rate', aggregable: true },
];

const ROWS = Array.from({ length: 12 }, (_, i) => ({
  company: `Company ${i % 3}`,
  driver: `Driver ${i % 4}`,
  rate: (i + 1) * 100,
}));

/** Every zone populated, so all three zone settings are on screen. */
const FULL: PivotModel = {
  rows: ['company'],
  columns: ['driver'],
  values: [{ key: 'rate', aggFn: 'sum' }],
};

async function openPanel() {
  render(<DataGrid columns={COLUMNS} data={ROWS} tableId="t" pivot fillHeight />);
  // The toolbar button only OPENS the panel; the switch inside pivots.
  const btn = screen.getByRole('button', { name: /^Pivot/ });
  await act(async () => { btn.click(); });
}

const box = (name: string) =>
  screen.getByRole('checkbox', { name }) as HTMLInputElement;

beforeEach(() => { stored = null; });
afterEach(cleanup);

describe('stored pivot settings survive the model rebuild', () => {
  it('carries hideEmptyColumns', async () => {
    stored = { enabled: true, model: { ...FULL, hideEmptyColumns: true } };
    await openPanel();
    expect(box('Hide columns with no values').checked).toBe(true);
  });

  it('carries pinRowLabels', async () => {
    stored = { enabled: true, model: { ...FULL, pinRowLabels: true } };
    await openPanel();
    expect(box('Keep row labels in view').checked).toBe(true);
  });

  it('carries pinTotals', async () => {
    stored = { enabled: true, model: { ...FULL, pinTotals: true } };
    await openPanel();
    expect(box('Keep Total column in view').checked).toBe(true);
  });

  it('carries drillDown', async () => {
    stored = { enabled: true, model: { ...FULL, drillDown: true } };
    await openPanel();
    expect(box('Open the rows behind a figure').checked).toBe(true);
  });

  it('leaves an unset flag off — the default is not "sticky true"', async () => {
    stored = { enabled: true, model: FULL };
    await openPanel();
    expect(box('Hide columns with no values').checked).toBe(false);
    expect(box('Keep row labels in view').checked).toBe(false);
    expect(box('Keep Total column in view').checked).toBe(false);
    expect(box('Open the rows behind a figure').checked).toBe(false);
  });
});
