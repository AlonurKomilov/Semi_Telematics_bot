/**
 * The search note — "why is this row here?"
 *
 * Global search deliberately reads HIDDEN columns, so an operator can
 * ask "which carrier mentioned hazmat?" without first knowing which of
 * 76 columns holds it.  The price is a result row whose every visible
 * cell reads "—": nothing on screen contains what was typed, which
 * reads as a broken search rather than a hidden hit.  These tests pin
 * the repayment — name the column, and offer to reveal it.
 *
 * The bug this prevents has a screenshot: Carrier Directory, "60"
 * typed, one row returned, every visible cell "—", the 60 sitting in a
 * hidden Solo Pay Rate column.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react';
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

// Captures the Undo toast so a test can invoke its action.
const toasts: { text: string; action?: { label: string; onClick: () => void } }[] = [];
vi.mock('sonner', () => ({
  toast: Object.assign(
    (text: string, opts?: { action?: { label: string; onClick: () => void } }) => {
      toasts.push({ text, action: opts?.action });
    },
    { success: () => {}, error: () => {} },
  ),
}));

// jsdom has no layout and no scrollIntoView — install a spy, which is
// exactly the call under test.
const scrolled: HTMLElement[] = [];
Element.prototype.scrollIntoView = function () { scrolled.push(this as HTMLElement); };

import DataGrid from './DataGrid';

interface Row extends Record<string, unknown> {
  id: number; name: string; solo?: string; struct?: string; summary?: string;
}

const COLUMNS: AnyColumn[] = [
  { key: 'name', label: 'Carrier' },
  { key: 'solo', label: 'Solo Pay Rate', defaultHidden: true },
  { key: 'struct', label: 'Pay Structure', defaultHidden: true },
];

const ROWS: Row[] = [{ id: 1, name: 'TESTS CARRIER', solo: '60' }];

const grid = (props: Record<string, unknown> = {}, data: Row[] = ROWS) => (
  <DataGrid columns={COLUMNS} data={data} searchKey="name" tableId="carriers" {...props} />
);

const search = (needle: string) => {
  fireEvent.change(screen.getByPlaceholderText('Search…'), { target: { value: needle } });
};

afterEach(() => { cleanup(); toasts.length = 0; scrolled.length = 0; });

describe('search note — a hit in a hidden column', () => {
  it('names the hidden column behind an otherwise unexplained row', () => {
    render(grid());
    search('60');
    // The row survived the filter…
    expect(screen.getByText('TESTS CARRIER')).toBeTruthy();
    // …and the note says why, naming the column to reveal.
    const note = screen.getByText(/only in a hidden column/);
    expect(note.textContent).toContain('1 row');
    expect(note.textContent).toContain('Solo Pay Rate');
  });

  it('reveals the column when asked, putting the match on screen', () => {
    render(grid());
    search('60');
    // Precondition: the value is genuinely not rendered yet.
    expect(screen.queryByRole('cell', { name: '60' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show column' }));
    expect(screen.getByRole('cell', { name: '60' })).toBeTruthy();
    // …and having explained itself, the note gets out of the way.
    expect(screen.queryByText(/hidden column/)).toBeNull();
  });

  // A note on every search is a nag, and a nag is ignored on the one
  // search where it mattered.
  it('stays quiet when a visible column already shows the match', () => {
    render(grid({}, [{ id: 1, name: 'CARRIER 60', solo: '60' }]));
    search('60');
    expect(screen.getByText('CARRIER 60')).toBeTruthy();
    expect(screen.queryByText(/hidden column/)).toBeNull();
  });

  it('counts only the rows a column can actually account for', () => {
    render(grid({}, [
      { id: 1, name: 'CARRIER 60' },            // visible hit — not counted
      { id: 2, name: 'A', solo: '60' },
      { id: 3, name: 'B', struct: '60/40' },
    ]));
    search('60');
    const note = screen.getByText(/only in hidden columns/);
    expect(note.textContent).toContain('2 rows');
    // Both named; they explain one row each, so column order decides.
    expect(note.textContent).toContain('Solo Pay Rate, Pay Structure');
    expect(screen.getByRole('button', { name: 'Show columns' })).toBeTruthy();
  });

  // Revealing a column cannot help a row that matched a searchKey field
  // no column renders, so no button is offered for one.
  it('says so, with no button, when no column explains the row', () => {
    render(
      <DataGrid
        columns={[COLUMNS[0], COLUMNS[1]]}
        data={[{ id: 1, name: 'A', summary: '60 months' }] as Row[]}
        searchKey={['name', 'summary']}
        tableId="carriers"
      />,
    );
    search('60');
    expect(screen.getByText(/shown as a column/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Show column/ })).toBeNull();
  });

  it('says nothing when there is no search', () => {
    render(grid());
    expect(screen.queryByText(/hidden column/)).toBeNull();
  });

  // Under manualFiltering the SERVER matched, so the grid does not know
  // what it matched on — a guess here would name the wrong column.
  it('stands down when the matching happened server-side', () => {
    render(grid({ manualFiltering: true, totalRows: 900 }));
    search('60');
    expect(screen.queryByText(/hidden column/)).toBeNull();
  });

  // A live region that mounts at the same moment as its text is often
  // not announced at all — which would leave a screen-reader user with
  // the unexplained rows and none of the explanation.
  it('keeps the live region mounted while empty', () => {
    const { container } = render(grid());
    const live = container.querySelector('[aria-live="polite"]');
    expect(live).toBeTruthy();
    expect(live!.textContent).toBe('');
  });
});

describe('search note — revealing a column', () => {
  // The action's whole purpose is "put the match on screen".  Setting
  // visibility is only half of that: on a 76-column grid the column
  // lands at its ordinal position and can be far off the right edge, so
  // without this the click completes with nothing visibly different.
  it('scrolls the revealed column into view, not just into the DOM', () => {
    render(grid());
    search('60');
    fireEvent.click(screen.getByRole('button', { name: 'Show column' }));
    expect(scrolled).toHaveLength(1);
    expect((scrolled[0] as HTMLElement).dataset.col).toBe('solo');
  });

  // A one-click link writes a PERSISTED, cross-device preference here.
  // Without Undo it silently keeps an operator's curated view changed
  // forever because they once searched for a number.
  it('offers Undo, and Undo puts the column back', () => {
    render(grid());
    search('60');
    fireEvent.click(screen.getByRole('button', { name: 'Show column' }));
    expect(screen.getByRole('cell', { name: '60' })).toBeTruthy();

    expect(toasts).toHaveLength(1);
    expect(toasts[0].text).toContain('Solo Pay Rate');
    expect(toasts[0].action?.label).toBe('Undo');

    // The note is gone by now — it explained itself and left — so Undo is
    // reached from the toast, which is the only handle that survives.
    // ``act`` because a toast action fires outside React's event system,
    // so nothing flushes the re-render for us the way fireEvent does.
    act(() => { toasts[0].action!.onClick(); });
    expect(screen.queryByRole('cell', { name: '60' })).toBeNull();
  });

  // No column to reveal is not the same as nothing to do — when the page
  // opens a row, that IS the recovery.
  it('points at the row when the page opens rows and no column can help', () => {
    render(
      <DataGrid
        columns={[COLUMNS[0], COLUMNS[1]]}
        data={[{ id: 1, name: 'A', summary: '60 months' }] as Row[]}
        searchKey={['name', 'summary']}
        tableId="carriers"
        onRowClick={() => {}}
      />,
    );
    search('60');
    expect(screen.getByText(/Open the row to see it/)).toBeTruthy();
  });

  it('does not point at a row the page cannot open', () => {
    render(
      <DataGrid
        columns={[COLUMNS[0], COLUMNS[1]]}
        data={[{ id: 1, name: 'A', summary: '60 months' }] as Row[]}
        searchKey={['name', 'summary']}
        tableId="carriers"
      />,
    );
    search('60');
    expect(screen.getByText(/shown as a column/)).toBeTruthy();
    expect(screen.queryByText(/Open the row/)).toBeNull();
  });
});
