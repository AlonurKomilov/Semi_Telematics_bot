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

/** ``hideEmptyColumns`` is about BUCKETS, not any assigned field, so it
 *  is the one setting still on a zone's ⋮. */
async function zoneSetting(zone: string, label: string) {
  const trigger = screen.getByRole('button', { name: `${zone} settings` });
  await act(async () => { trigger.click(); });
  const item = screen.getByRole('menuitem', { name: new RegExp(label) });
  const checked = !!item.querySelector('svg');
  return { checked, item };
}

/** Pin lives on the FIELD's ⋮, where list mode keeps it too.
 *
 *  Closes any menu already open first: these menus portal into the body
 *  and do NOT dismiss when another trigger is clicked programmatically,
 *  so reading two fields in one test would otherwise match the same item
 *  in two live menus. */
async function fieldSetting(field: string, label: string) {
  await act(async () => {
    document.body.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
    );
  });
  const trigger = screen.getByRole('button', { name: `${field} options` });
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
    expect((await fieldSetting('Company', 'Pin row labels')).checked).toBe(true);
  });

  it('carries pinTotals', async () => {
    stored = { enabled: true, model: { ...FULL, pinTotals: true } };
    await openPanel();
    expect((await fieldSetting('Rate', 'Pin Total column')).checked).toBe(true);
  });

  it('carries drillDown', async () => {
    stored = { enabled: true, model: { ...FULL, drillDown: true } };
    await openPanel();
    expect(drillBtn().getAttribute('aria-pressed')).toBe('true');
  });

  it('leaves an unset flag off — the default is not "sticky true"', async () => {
    stored = { enabled: true, model: FULL };
    await openPanel();
    expect((await fieldSetting('Company', 'Pin row labels')).checked).toBe(false);
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
    // Not on the measure that produces the figures, either.
    const rate = screen.getByRole('button', { name: 'Rate options' });
    await act(async () => { rate.click(); });
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

  it('keeps settings out of the inline field list entirely', async () => {
    // COLUMNS used to stack five identical checkboxes in one run: one
    // governing the zone, four governing which fields contribute.  Same
    // shape, same x, two unrelated meanings — you had to read every
    // label to tell them apart.  Every setting is a MENU item now, so
    // the only inline control beside a field is that field's own tick.
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

  it('shows the SAME pin state on every row field — they share one column', async () => {
    // Company and Customer are not two columns: `rowFieldLabel` joins the
    // row fields into one header and the body is a tree inside a single
    // cell.  So the pin is one setting, reachable from either field's ⋮,
    // and it must not look like a per-field freeze.
    stored = {
      enabled: true,
      // ``columns: []`` because Driver is the second ROW field here — left
      // on the column axis too it would render two "Driver options"
      // buttons and the query would be ambiguous.
      model: {
        ...FULL, rows: ['company', 'driver'], columns: [], pinRowLabels: true,
      },
    };
    await openPanel();
    expect((await fieldSetting('Company', 'Pin row labels')).checked).toBe(true);
    expect((await fieldSetting('Driver', 'Pin row labels')).checked).toBe(true);
  });

  it('toggles from the field menu and the change sticks', async () => {
    stored = { enabled: true, model: FULL };
    await openPanel();
    const { item } = await fieldSetting('Company', 'Pin row labels');
    await act(async () => { (item as HTMLElement).click(); });
    expect((await fieldSetting('Company', 'Pin row labels')).checked).toBe(true);
  });

  it('offers no Total-column pin when there is no column dimension', async () => {
    // Nothing to total across, so no Total column exists — the item would
    // be a dead end, offered and then unable to do anything.
    stored = { enabled: true, model: { ...FULL, columns: [] } };
    await openPanel();
    const rate = screen.getByRole('button', { name: 'Rate options' });
    await act(async () => { rate.click(); });
    expect(screen.queryByRole('menuitem', { name: /^Pin/ })).toBeNull();
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
    expect((await fieldSetting('Rate', 'Pin 2 Total columns')).checked).toBe(false);
  });

  it('stays singular with one measure', async () => {
    stored = { enabled: true, model: FULL };
    await openPanel();
    expect((await fieldSetting('Rate', 'Pin Total column')).checked).toBe(false);
  });
});
