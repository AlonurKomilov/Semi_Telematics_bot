/**
 * Controlled view-state on DataGrid.
 *
 * These props exist so a page whose data exceeds one fetch can filter on
 * the SERVER while the filter UI stays in the grid.  Two properties have
 * to hold, and they pull in opposite directions:
 *
 *   1. Nothing changes for the ~10 grids that don't pass them.  The
 *      default is still "the grid owns its state and filters its rows".
 *   2. When a page DOES take control, the grid must stop deciding —
 *      it reports intent and renders what it was handed, rather than
 *      quietly narrowing a capped page and calling that the answer.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import type { AnyColumn } from '../../types';

// jsdom has no ResizeObserver; DataGrid measures column widths with one.
// A no-op is enough — these tests assert state plumbing, not layout.
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
}));
vi.mock('../../hooks/useTimezone', () => ({ useTimezone: () => 'UTC' }));
// Per-table preferences resolve their default from the registry, so the
// mock reads the REAL defaults rather than mirroring them — a hand-copied
// list drifts, and a wrong default surfaces as an inscrutable "Cannot use
// 'in' operator on null" deep inside column layout.
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

const ROWS: Row[] = [
  { id: 1, type: 'fault', name: 'Truck 1' },
  { id: 2, type: 'fault', name: 'Truck 2' },
  { id: 3, type: 'health', name: 'Truck 3' },
];

const COLUMNS: AnyColumn[] = [
  { key: 'name', label: 'Vehicle', sortable: true },
  { key: 'type', label: 'Type', filterable: true, filterMode: 'select' },
];

const bodyText = () => document.querySelector('tbody')?.textContent ?? '';

afterEach(cleanup);

describe('DataGrid — uncontrolled (every existing grid)', () => {
  it('owns its filter state and filters its own rows', () => {
    render(<DataGrid columns={COLUMNS} data={ROWS} />);
    // All three rows render; the grid was given no filter.
    expect(bodyText()).toContain('Truck 1');
    expect(bodyText()).toContain('Truck 3');
  });

  it('derives select-filter options from the loaded rows when none are declared', () => {
    // The pre-existing behaviour: options come from the data in hand.
    render(<DataGrid columns={COLUMNS} data={ROWS} />);
    expect(bodyText()).toContain('Truck 2');
  });
});

describe('DataGrid — controlled column filters', () => {
  it('renders the filters it is handed and reports changes instead of self-applying', () => {
    const onChange = vi.fn();
    render(
      <DataGrid
        columns={COLUMNS}
        data={ROWS}
        columnFilters={[{ id: 'type', value: ['fault'] }]}
        onColumnFiltersChange={onChange}
        manualFiltering
      />,
    );
    // manualFiltering: rows arrived pre-filtered, so the grid must NOT
    // filter again.  Handing it all three rows with a 'fault' filter
    // active must still show all three — otherwise a server-filtered
    // page would be double-filtered and lose rows.
    expect(bodyText()).toContain('Truck 3');
  });

  it('a controlled grid does not mutate its own filter state', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <DataGrid
        columns={COLUMNS}
        data={ROWS}
        columnFilters={[]}
        onColumnFiltersChange={onChange}
        manualFiltering
      />,
    );
    // Re-render with a different controlled value: the grid must follow
    // the prop, not a copy it kept.
    rerender(
      <DataGrid
        columns={COLUMNS}
        data={[ROWS[0]]}
        columnFilters={[{ id: 'type', value: ['fault'] }]}
        onColumnFiltersChange={onChange}
        manualFiltering
      />,
    );
    expect(bodyText()).toContain('Truck 1');
    expect(bodyText()).not.toContain('Truck 3');
  });
});

describe('DataGrid — manualFiltering keeps local search alive', () => {
  it('still filters by the search box while ignoring the column filters', async () => {
    // tanstack's own ``manualFiltering`` option short-circuits the whole
    // filtered row model — and GLOBAL SEARCH lives in there too, so using
    // it would silently disable the search box.  We withhold the column
    // filters from the table instead.  This test is the guard.
    render(
      <DataGrid
        columns={COLUMNS}
        data={ROWS}
        searchKey={['name']}
        columnFilters={[{ id: 'type', value: ['fault'] }]}
        onColumnFiltersChange={() => {}}
        manualFiltering
      />,
    );
    const box = document.querySelector('input[type="text"], input:not([type])') as HTMLInputElement;
    expect(box).toBeTruthy();
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value',
      )!.set!;
      setter.call(box, 'Truck 3');
      box.dispatchEvent(new Event('input', { bubbles: true }));
    });
    // Search narrowed to Truck 3 — even though the 'fault' column filter
    // (which Truck 3 fails) is active and deliberately not applied.
    expect(bodyText()).toContain('Truck 3');
    expect(bodyText()).not.toContain('Truck 1');
  });
});

describe('DataGrid — manualFiltering still REPORTS the active filter', () => {
  it('marks a filtered column as filtered', () => {
    // Not filtering the rows must not mean pretending there is no filter.
    // The menus, the header tint and the 3-dot badge all read the value
    // back through tanstack's own column state — so the first attempt at
    // this (withholding the filters from the table) left an active filter
    // showing a chip on the toolbar while the column looked untouched and
    // its menu opened with nothing ticked.
    const { container } = render(
      <DataGrid
        columns={COLUMNS}
        data={ROWS}
        columnFilters={[{ id: 'type', value: ['fault'] }]}
        onColumnFiltersChange={() => {}}
        manualFiltering
      />,
    );
    const typeHeader = Array.from(container.querySelectorAll('th'))
      .find(th => th.textContent?.includes('Type'));
    expect(typeHeader?.innerHTML).toContain('text-primary');
  });

  it('and does NOT mark an unfiltered column (negative control)', () => {
    // Guards the assertion above from passing on a header that is tinted
    // for some unrelated reason.
    const { container } = render(
      <DataGrid
        columns={COLUMNS}
        data={ROWS}
        columnFilters={[]}
        onColumnFiltersChange={() => {}}
        manualFiltering
      />,
    );
    const typeHeader = Array.from(container.querySelectorAll('th'))
      .find(th => th.textContent?.includes('Type'));
    expect(typeHeader?.innerHTML).not.toContain('text-primary');
  });
});

describe('DataGrid — declared filter options', () => {
  it('uses filterOptions verbatim rather than deriving from loaded rows', () => {
    // The rows loaded are ALL faults (as they would be after a server
    // filter).  Derivation would offer only "fault" and strand the
    // operator; the declared list keeps every choice reachable.
    const cols: AnyColumn[] = [
      { key: 'name', label: 'Vehicle' },
      {
        key: 'type', label: 'Type', filterable: true, filterMode: 'select',
        filterOptions: [
          { value: 'fault', label: 'Fault' },
          { value: 'health', label: 'Health' },
          { value: 'fuel', label: 'Fuel' },
        ],
      },
    ];
    render(<DataGrid columns={cols} data={ROWS.filter(r => r.type === 'fault')} manualFiltering />);
    expect(bodyText()).toContain('Truck 1');
    expect(bodyText()).not.toContain('Truck 3');
  });
});

describe('DataGrid — segment counts', () => {
  const SEGMENTS = [
    { key: 'open', label: 'Open' },
    { key: 'done', label: 'Done' },
  ];

  it('prefers server-supplied counts over a tally of loaded rows', () => {
    render(
      <DataGrid
        columns={COLUMNS}
        data={ROWS}
        tableId="t1"
        segments={SEGMENTS}
        segmentCounts={{ open: 3938, done: 12 }}
      />,
    );
    // The grid holds 3 rows but the queue has 3,938 — the badge must
    // report the queue, not the page.  A tab badge is the most
    // authoritative-looking number on the page.
    expect(document.body.textContent).toContain('3,938');   // locale-formatted
    expect(document.body.textContent).toContain('12');
    // ...and NOT the 3 rows it happens to hold.
    expect(document.body.textContent).not.toMatch(/Open\s*3(?!,)/);
  });

  it('falls back to the local tally for segments the server did not count', () => {
    render(
      <DataGrid
        columns={COLUMNS}
        data={ROWS}
        tableId="t2"
        segments={[{ key: 'all', label: 'All' }]}
      />,
    );
    expect(document.body.textContent).toContain('3');
  });
});

describe('DataGrid — controlled segment selection', () => {
  const SEGMENTS = [
    { key: 'active', label: 'Active' },
    { key: 'acked', label: 'Acknowledged' },
  ];

  it('follows the segmentKey prop and reports clicks outward', async () => {
    const onSegment = vi.fn();
    render(
      <DataGrid
        columns={COLUMNS}
        data={ROWS}
        tableId="t3"
        segments={SEGMENTS}
        segmentKey="acked"
        onSegmentChange={onSegment}
      />,
    );
    await act(async () => { screen.getByText('Active').click(); });
    // Reports intent; does NOT switch itself — the page owns the value.
    // A built-in segment carries no criteria: the page already knows what
    // its own segment keys mean.  (Saved tabs DO carry theirs.)
    expect(onSegment).toHaveBeenCalledWith('active', undefined);
  });
});
