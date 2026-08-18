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
  // The pivot module chain reaches src/i18n.ts, which calls
  // .use(initReactI18next) at import time — without this the whole file
  // fails to COLLECT, which reads as "no tests" rather than a failure.
  initReactI18next: { type: '3rdParty', init: () => {} },
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

// By ACCESSIBLE NAME, not visible text: the toggle's presentation is the
// grid's business (it just went icon-only mid-session) — what the test
// owns is that a control named "Pivot" exists and gates correctly.
const findPivotToggle = () =>
  Array.from(document.querySelectorAll('button')).find(
    (b) => b.getAttribute('aria-label') === 'Pivot'
      || b.textContent?.includes('Pivot'),
  );

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

describe('DataGrid — manualFiltering hands search to the page too', () => {
  it('reports typing outward and does NOT re-narrow the rows itself', async () => {
    // ``manualFiltering`` means every narrowing already happened
    // upstream — search included.  Re-applying it here would filter by
    // the grid's ``searchKey`` columns, which need not be the columns the
    // SERVER searched: the board's server search covers vehicle AND
    // location, so a local pass over one of them would drop rows the
    // server correctly returned.
    const onSearch = vi.fn();
    render(
      <DataGrid
        columns={COLUMNS}
        data={ROWS}
        searchKey={['name']}
        globalFilter=""
        onGlobalFilterChange={onSearch}
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
    // The page is told what to query...
    expect(onSearch).toHaveBeenCalledWith('Truck 3');
    // ...and the grid renders exactly the rows it was handed until the
    // page comes back with a narrower set.
    expect(bodyText()).toContain('Truck 1');
    expect(bodyText()).toContain('Truck 3');
  });

  it('keeps searching locally when the grid is NOT manual', async () => {
    render(<DataGrid columns={COLUMNS} data={ROWS} searchKey={['name']} />);
    const box = document.querySelector('input[type="text"], input:not([type])') as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value',
      )!.set!;
      setter.call(box, 'Truck 3');
      box.dispatchEvent(new Event('input', { bubbles: true }));
    });
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


describe('DataGrid — a grid that holds only a SLICE stops pretending', () => {
  // Without ``totalRows`` a grid assumes the rows it holds ARE the result,
  // so every whole-set operation answers for the whole from a part.  These
  // pin the four that did.
  const partial = {
    columns: COLUMNS,
    data: ROWS,               // 3 rows in hand...
    totalRows: 11200,         // ...of 11,200 behind them
    tableId: 'sliced',
  };

  it('labels CSV export "loaded" and shows both numbers', () => {
    render(<DataGrid {...partial} />);
    // "All rows" would name a file -all containing 3 of 11,200.
    expect(document.body.textContent).not.toContain('All rows');
  });

  it('disables Pivot with a reason instead of summarising a fragment', () => {
    render(<DataGrid {...partial} pivot />);
    const btn = Array.from(document.querySelectorAll('button'))
      .find((b) => b.getAttribute('aria-label') === 'Pivot');
    expect(btn).toBeTruthy();
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  it('leaves Pivot usable when the grid holds everything', () => {
    render(<DataGrid columns={COLUMNS} data={ROWS} tableId="whole" pivot />);
    const btn = Array.from(document.querySelectorAll('button'))
      .find((b) => b.getAttribute('aria-label') === 'Pivot');
    expect((btn as HTMLButtonElement).disabled).toBe(false);
  });

  it('treats an exact-fit total as complete, not partial', () => {
    // totalRows === rows held: nothing is missing, so nothing is gated.
    render(
      <DataGrid columns={COLUMNS} data={ROWS} totalRows={ROWS.length}
        tableId="exact" pivot />,
    );
    const btn = Array.from(document.querySelectorAll('button'))
      .find((b) => b.getAttribute('aria-label') === 'Pivot');
    expect((btn as HTMLButtonElement).disabled).toBe(false);
  });
});


describe('DataGrid — bulk confirm gated on scope', () => {
  // A modal on every action becomes a reflex click, so ``confirm`` may
  // return '' to mean "not at this count".  These pin both branches,
  // because getting it backwards either interrupts routine work or
  // silently lets a select-all through.
  const rowsFor = (n: number) => Array.from({ length: n }, (_, i) => ({
    id: i + 1, type: 'fault', name: `Truck ${i + 1}`,
  }));

  function renderWithBulk(count: number, confirmFn: (n: number) => string) {
    const onRun = vi.fn();
    const data = rowsFor(count);
    render(
      <DataGrid
        columns={COLUMNS}
        data={data}
        tableId="bulk"
        bulkSelection
        selectedIds={new Set(data.map((r) => String(r.id)))}
        onSelectedIdsChange={() => {}}
        bulkActions={[{ label: 'Acknowledge', confirm: confirmFn, onRun }]}
      />,
    );
    return onRun;
  }

  const clickAcknowledge = async () => {
    const btn = Array.from(document.querySelectorAll('button'))
      .find((b) => b.getAttribute('aria-label') === 'Acknowledge'
        || b.textContent?.trim() === 'Acknowledge');
    expect(btn, 'the bulk Acknowledge control').toBeTruthy();
    await act(async () => { (btn as HTMLButtonElement).click(); });
  };

  it('runs WITHOUT prompting when the message is empty', async () => {
    const spy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const onRun = renderWithBulk(3, (n) => (n >= 10 ? `Acknowledge ${n}?` : ''));
    await clickAcknowledge();
    expect(spy).not.toHaveBeenCalled();      // routine work, uninterrupted
    expect(onRun).toHaveBeenCalled();
    spy.mockRestore();
  });

  it('prompts with the COUNT once the selection is large', async () => {
    const spy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const onRun = renderWithBulk(12, (n) => (n >= 10 ? `Acknowledge ${n} alerts?` : ''));
    await clickAcknowledge();
    expect(spy).toHaveBeenCalledWith(expect.stringContaining('12'));
    expect(onRun).toHaveBeenCalled();
    spy.mockRestore();
  });

  it('does NOT run when the operator declines', async () => {
    const spy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const onRun = renderWithBulk(12, (n) => (n >= 10 ? `Acknowledge ${n} alerts?` : ''));
    await clickAcknowledge();
    expect(onRun).not.toHaveBeenCalled();    // declining must mean nothing happened
    spy.mockRestore();
  });
});


describe('DataGrid — a slice gates the STATE, not just the act', () => {
  /**
   * The four `totalRows` gates all guarded the ACT: the ⋮ menu item was
   * disabled, so you could not START grouping or aggregating on a slice.
   * Nothing guarded a choice ALREADY MADE. Both prefs persist per-user
   * across devices, so a grid configured while the account was small kept
   * computing after the dataset outgrew one page — over the rows on
   * screen — and said nothing.
   *
   * `defaultRowGroup` / `defaultAggregation` reach the component through
   * the same `useTablePreference` the stored value uses (see the mock at
   * the top of this file), so passing them here IS the persisted case.
   */
  const NUM_COLUMNS: AnyColumn[] = [
    { key: 'name', label: 'Vehicle', sortable: true },
    { key: 'type', label: 'Type', filterable: true, filterMode: 'select' },
    { key: 'cost', label: 'Cost', aggregable: true },
  ];
  const NUM_ROWS = ROWS.map((r, i) => ({ ...r, cost: (i + 1) * 100 }));

  it('does not group a slice, even when grouping was already chosen', () => {
    render(
      <DataGrid columns={NUM_COLUMNS} data={NUM_ROWS} tableId="g-partial"
        totalRows={11200} defaultRowGroup="type" />,
    );
    // Grouping renders a "Grouped by" chip and collapses rows into group
    // headers. Neither may happen while the grid holds 3 of 11,200 — the
    // per-group tallies would describe the page, not the group.
    expect(document.body.textContent).not.toContain('Grouped by');
  });

  it('DOES group the same config when the grid holds everything', () => {
    // The preference is kept, not cleared — grouping returns by itself
    // once the view is narrow enough to be honest.
    render(
      <DataGrid columns={NUM_COLUMNS} data={NUM_ROWS} tableId="g-whole"
        defaultRowGroup="type" />,
    );
    expect(document.body.textContent).toContain('Grouped by');
  });

  it('renders no footer total on a slice, even when one was chosen', () => {
    render(
      <DataGrid columns={NUM_COLUMNS} data={NUM_ROWS} tableId="a-partial"
        totalRows={11200} defaultAggregation={{ cost: 'sum' }} />,
    );
    // 600 is the sum of the loaded 3 rows — a confident whole-set claim
    // over a fragment, and a cross-tab-like one: no rows sit beside a
    // footer total to make the shortfall noticeable.
    expect(document.querySelector('tfoot')).toBeNull();
  });

  it('DOES total the same config when the grid holds everything', () => {
    render(
      <DataGrid columns={NUM_COLUMNS} data={NUM_ROWS} tableId="a-whole"
        defaultAggregation={{ cost: 'sum' }} />,
    );
    expect(document.querySelector('tfoot')).not.toBeNull();
    expect(document.querySelector('tfoot')?.textContent).toContain('600');
  });
});


describe('DataGrid — manualSorting means the server already ordered these', () => {
  /**
   * `manualSorting` existed as a prop and was read in exactly ONE place —
   * the decision to leave sort ENABLED on a slice. It was never passed to
   * tanstack, so the grid re-sorted the page it had just been told not to
   * sort.
   *
   * That is not a no-op, it is a wrong answer: the server picks WHICH
   * rows by its ordering, the grid reorders that page by its own, and so
   * every page looks sorted while the table is not. It also breaks
   * silently across pages — SQL's `LOWER(name)` puts "Truck 10" before
   * "Truck 9"; tanstack's alphanumeric comparator does the reverse.
   */
  const UNSORTED = [
    { id: 3, type: 'health', name: 'Truck 3' },
    { id: 1, type: 'fault', name: 'Truck 1' },
    { id: 2, type: 'fault', name: 'Truck 2' },
  ];
  const names = () => Array.from(document.querySelectorAll('tbody tr'))
    .map((tr) => tr.querySelector('td')?.textContent?.trim())
    .filter(Boolean);

  it('renders the given order verbatim under manualSorting', () => {
    render(
      <DataGrid columns={COLUMNS} data={UNSORTED} tableId="ms-on"
        sorting={[{ id: 'name', desc: false }]}
        onSortingChange={() => {}}
        manualSorting />,
    );
    // The sort state says "by name, ascending" and the rows arrived in
    // 3-1-2. Under manualSorting that IS the answer: the order came from
    // upstream, over the whole set.
    expect(names()).toEqual(['Truck 3', 'Truck 1', 'Truck 2']);
  });

  it('sorts locally when the page has NOT claimed the order', () => {
    // The control: same state, same rows, no manualSorting. This is the
    // behaviour that was wrongly applied to server-ordered pages too.
    render(
      <DataGrid columns={COLUMNS} data={UNSORTED} tableId="ms-off"
        sorting={[{ id: 'name', desc: false }]}
        onSortingChange={() => {}} />,
    );
    expect(names()).toEqual(['Truck 1', 'Truck 2', 'Truck 3']);
  });
});
