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
  { key: 'miles', label: 'Miles', aggregable: true },
];

const ROWS = Array.from({ length: 12 }, (_, i) => ({
  company: `Company ${i % 3}`,
  driver: `Driver ${i % 4}`,
  rate: (i + 1) * 100,
  miles: (i + 1) * 10,
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

/** Zone settings live in the ZONE's own ⋮ — the same place list mode
 *  keeps Pin and Hide.  So reading one means opening that menu first. */
async function zoneSetting(zone: string, label: string) {
  const trigger = screen.getByRole('button', { name: `${zone} settings` });
  await act(async () => { trigger.click(); });
  const item = screen.getByRole('menuitem', { name: new RegExp(label) });
  // A check mark is the on-state; the icon slot is filled either way so
  // the label can't shift sideways as it toggles.
  const checked = !!item.querySelector('svg');
  return { checked, item };
}
/** Drilling is report-wide, so its control is a pressed BUTTON in the
 *  panel header rather than anything zone-scoped. */
const drillBtn = () =>
  screen.getByRole('button', { name: 'Open the rows behind a figure' });

beforeEach(() => { stored = null; });
afterEach(cleanup);

describe('stored pivot settings survive the model rebuild', () => {
  it('carries hideEmptyColumns', async () => {
    stored = { enabled: true, model: { ...FULL, hideEmptyColumns: true } };
    await openPanel();
    expect((await zoneSetting('Columns', 'Hide columns with no values')).checked).toBe(true);
  });

  it('carries pinRowLabels', async () => {
    stored = { enabled: true, model: { ...FULL, pinRowLabels: true } };
    await openPanel();
    expect((await zoneSetting('Rows', 'Pin row labels')).checked).toBe(true);
  });

  it('carries pinTotals', async () => {
    stored = { enabled: true, model: { ...FULL, pinTotals: true } };
    await openPanel();
    expect((await zoneSetting('Values', 'Pin Total column')).checked).toBe(true);
  });

  it('carries drillDown', async () => {
    stored = { enabled: true, model: { ...FULL, drillDown: true } };
    await openPanel();
    expect(drillBtn().getAttribute('aria-pressed')).toBe('true');
  });

  it('leaves an unset flag off — the default is not "sticky true"', async () => {
    stored = { enabled: true, model: FULL };
    await openPanel();
    expect((await zoneSetting('Rows', 'Pin row labels')).checked).toBe(false);
    expect(drillBtn().getAttribute('aria-pressed')).toBe('false');
  });
});

describe('the panel says what each control governs', () => {
  it('drilling is a header control, NOT a zone setting', async () => {
    // It began in VALUES, when only value cells drilled.  Once the Total
    // column and the footer became drillable it governed the whole
    // matrix, and a report-wide behaviour parked inside one zone reads
    // as though it only applies there.
    stored = { enabled: true, model: FULL };
    await openPanel();
    expect(drillBtn()).toBeTruthy();
    const values = screen.getByRole('button', { name: 'Values settings' });
    await act(async () => { values.click(); });
    expect(screen.queryByRole('menuitem', { name: /Open the rows behind/ })).toBeNull();
  });

  it('is disabled until the grid is actually pivoted', async () => {
    // The panel renders while pivot is off (that is where you switch it
    // on), and there is no report to drill into yet.  Disabled with a
    // reason, never hidden.
    stored = { enabled: false, model: FULL };
    await openPanel();
    expect((drillBtn() as HTMLButtonElement).disabled).toBe(true);
  });

  it('keeps zone settings OUT of the field list entirely', async () => {
    // COLUMNS used to stack five identical checkboxes in one run: one
    // governing the zone, four governing which fields contribute.  Same
    // shape, same x, two unrelated meanings — you had to read every
    // label to tell them apart.  The zone's settings live in the zone's
    // ⋮ now, so the only controls beside a field are that field's own.
    stored = { enabled: true, model: FULL };
    await openPanel();
    for (const name of [
      'Pin row labels', 'Pin Total column',
      'Hide columns with no values',
    ]) {
      expect(screen.queryByRole('checkbox', { name })).toBeNull();
      expect(screen.queryByRole('switch', { name })).toBeNull();
    }
    // A FIELD is still a checkbox — membership, which is what a checkbox
    // is for.
    expect(screen.getByRole('checkbox', { name: /Include Company/ })).toBeTruthy();
  });

  it('toggles from the zone menu and the change sticks', async () => {
    stored = { enabled: true, model: FULL };
    await openPanel();
    const { item } = await zoneSetting('Rows', 'Pin row labels');
    await act(async () => { (item as HTMLElement).click(); });
    expect((await zoneSetting('Rows', 'Pin row labels')).checked).toBe(true);
  });

  it('offers no Total-column setting when there is no column dimension', async () => {
    // Nothing to total across, so the item would be a dead end — offered
    // and then unable to do anything.
    stored = { enabled: true, model: { ...FULL, columns: [] } };
    await openPanel();
    expect(screen.queryByRole('button', { name: 'Values settings' })).toBeNull();
  });

  it('counts the Total columns it will pin — there is one per measure', async () => {
    // `totalLabels` maps over the value fields, so two measures means two
    // Total columns.  A hard-coded "Pin Total column" under-described the
    // setting the moment a second measure was assigned.
    stored = {
      enabled: true,
      model: {
        ...FULL,
        values: [
          { key: 'rate', aggFn: 'sum' },
          { key: 'miles', aggFn: 'sum' },
        ],
      },
    };
    await openPanel();
    expect((await zoneSetting('Values', 'Pin 2 Total columns')).checked).toBe(false);
  });

  it('stays singular with one measure', async () => {
    stored = { enabled: true, model: FULL };
    await openPanel();
    expect((await zoneSetting('Values', 'Pin Total column')).checked).toBe(false);
  });
});
