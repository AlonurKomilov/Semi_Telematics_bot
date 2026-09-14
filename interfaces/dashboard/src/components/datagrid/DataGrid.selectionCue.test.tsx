/**
 * Which selection changes are something a person DID.
 *
 * `setSelectedRowIds` has ten call sites in this file and only four are
 * an act: a range gathered with Shift, the select-all box, a group box,
 * and the Clear button. The other six are the grid changing its own
 * mind — a prune when the filter narrows, a plain click resetting an
 * in-progress selection, the reset after a bulk action runs — or they
 * are ONE ROW, which happens a hundred to four hundred times in a shift
 * and is already visible under the hand that did it.
 *
 * Wiring the cue at the shared setter would have been one line and
 * caught all ten. This file is what keeps it at four.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { AnyColumn } from '../../types';

const { playActCue } = vi.hoisted(() => ({ playActCue: vi.fn() }));
vi.mock('../../mods/sound/cue', () => ({ playActCue }));

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
// The shape `DataGrid.searchNote.test.tsx` already proved: the registry
// is imported for real so a table part keeps its declared default, and
// the hooks are stateful so a toggle actually toggles.
vi.mock('../../preferences', async () => {
  const actual = await vi.importActual<
    typeof import('../../preferences/registry')
  >('../../preferences/registry');
  const { useState } = await import('react');
  return {
    useSyncLoaded: () => true,
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
    preferences: { get: () => false },
  };
});
vi.mock('sonner', () => ({
  toast: Object.assign(() => {}, { success: () => {}, error: () => {} }),
}));
Element.prototype.scrollIntoView = function () {};

import DataGrid from './DataGrid';

interface Row extends Record<string, unknown> { id: number; name: string }
const COLUMNS: AnyColumn[] = [{ key: 'name', label: 'Carrier' }];
const ROWS: Row[] = [
  { id: 1, name: 'ALPHA' }, { id: 2, name: 'BRAVO' }, { id: 3, name: 'CHARLIE' },
];

const grid = () => (
  <DataGrid columns={COLUMNS} data={ROWS} searchKey="name" tableId="sel" bulkSelection />
);

const box = (label: string) => screen.getByLabelText(label) as HTMLInputElement;

beforeEach(() => { playActCue.mockClear(); });
afterEach(cleanup);

describe('a set gathered, and a set dropped', () => {
  it('select-all is one act, whatever it takes', () => {
    render(grid());
    fireEvent.click(box('Select all rows'));
    expect(playActCue).toHaveBeenCalledWith('select_add');
    // THREE rows, ONE cue. The count is on screen; what the sound
    // reports is that a set exists now.
    expect(playActCue).toHaveBeenCalledTimes(1);
  });

  it('and un-ticking it drops the set', () => {
    render(grid());
    fireEvent.click(box('Select all rows'));
    playActCue.mockClear();
    fireEvent.click(box('Select all rows'));
    expect(playActCue).toHaveBeenCalledWith('select_clear');
  });

  it('and the Clear button says what it did, not that it was pressed', () => {
    render(grid());
    fireEvent.click(box('Select all rows'));
    playActCue.mockClear();
    fireEvent.click(screen.getByLabelText('Clear selection'));
    expect(playActCue).toHaveBeenCalledWith('select_clear');
  });
});

describe('what the grid does on its own account', () => {
  /**
   * The one that would have made this unusable. A row tick is the most
   * repeated act in the product and the tick is already on screen under
   * the hand that made it.
   */
  it('ticking one row is silent', () => {
    render(grid());
    // Three rows, three identical labels — the first will do.
    fireEvent.click(screen.getAllByLabelText('Select row')[0]);
    expect(playActCue, 'a row tick spoke — at 100-400 a shift').not.toHaveBeenCalled();
  });

  /**
   * The grid drops selected rows that a narrowing filter removed. Nobody
   * clicked anything, and a cue meaning "you gathered a set" for the app
   * dropping one is a sound with no act behind it.
   */
  it('and the prune when a filter narrows is silent', () => {
    render(grid());
    fireEvent.click(box('Select all rows'));
    playActCue.mockClear();
    fireEvent.change(screen.getByPlaceholderText('Search…'), { target: { value: 'ALPHA' } });
    expect(playActCue, 'the grid announced its own bookkeeping').not.toHaveBeenCalled();
  });
});

describe('the wiring is at the acts, not at the setter', () => {
  it('so the cue cannot be moved to the shared funnel by accident', () => {
    // Four raises, and the setter has ten call sites. If somebody moves
    // this into `setSelectedRowIds` the count goes to one and every
    // assertion above about silence starts failing — but only if the
    // number is written down, so it is.
    const code = readFileSync(join(__dirname, 'DataGrid.tsx'), 'utf8');
    expect((code.match(/playActCue\(/g) ?? []).length,
      'the selection cues moved. Four acts deserve one: a Shift range, select-all, '
      + 'a group box, and Clear. The other six writes are the grid changing its own '
      + 'mind or one row.').toBe(4);
  });
});
