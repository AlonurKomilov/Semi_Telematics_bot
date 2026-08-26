/**
 * CalloutGroup — one statement, many occurrences.
 *
 * The Vehicles page showed four device questions as four full strips.
 * Three were the same question about three different trucks, so the
 * same three sentences were printed three times and the page's own
 * content was pushed off the screen.
 *
 * Collapsing was not available and should not have been: those keys
 * carry `dismiss: 'none'` because a question waiting on an answer must
 * not be dismissable.  The repetition was the waste.
 *
 * What is worth guarding is that the shared/varying split is COMPUTED
 * from the resolved values rather than declared anywhere — that is
 * what stops it going stale when a callout gains or loses a line.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  render, screen, cleanup, fireEvent, renderHook, act,
} from '@testing-library/react';

afterEach(cleanup);

const LABELS: Record<string, string> = {
  'callout.labels.where': 'Where',
  'callout.labels.changed': 'Changed',
  'callout.labels.why': 'Why',
  'callout.labels.affects': 'Affects',
};

// Per-viewer preference store, stubbed so the hook tests can watch
// exactly which ids get written.  Hoisted with the other mocks: a
// vi.doMock inside a describe runs after this file's static imports
// have already pulled in the real module.
const prefs: { value: Record<string, number> } = { value: {} };
vi.mock('../../preferences', () => ({
  usePreference: () => ({
    value: prefs.value,
    setValue: (v: Record<string, number>) => { prefs.value = v; },
  }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, vars: Record<string, string> = {}) =>
      LABELS[key]
      ?? (key === 'callout.labels.show_all' ? `Show all ${vars.count}` : key),
  }),
}));

// The group resolves through the pure resolver, so this stands in for
// the copy: same why/affects for every truck, different where/changed.
vi.mock('./useCallout', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./useCallout')>();
  return {
    ...actual,
    resolveCallout: (_t: unknown, c: { key: string; params?: Record<string, string> }) => ({
      key: c.key,
      tone: 'warn' as const,
      title: 'VIN changed: is this a different truck?',
      short: 'VIN changed',
      explanation: 'A VIN names one physical truck.',
      Icon: () => null,
      lines: [
        { name: 'where' as const, label: 'Where', value: c.params!.unit },
        { name: 'changed' as const, label: 'Changed', value: c.params!.vins },
        { name: 'why' as const, label: 'Why',
          value: 'A VIN names one physical truck.' },
        { name: 'affects' as const, label: 'Affects',
          value: 'Mileage · Maintenance history · Inspections' },
      ].filter((l) => l.value),
    }),
  };
});

import CalloutGroup from './CalloutGroup';
import { useGroupDismissal, useDismissal } from './useDismissal';

const truck = (unit: string, vins: string) => ({ unit, vins });
const asCallout = (t: { unit: string; vins: string }) => ({
  key: 'vehicle.vin_changed', params: { unit: t.unit, vins: t.vins },
});

const THREE = [
  truck('229', '1JJV532D8TL644820 → 3AKJHHDR6TSWP7980'),
  truck('254', '1FUJHHDR3VLXK1416 → 1FUJHHDR5VLXK1417'),
  truck('128', '4V4NC9EH8KN196862 → 3AKJGLDV5GSGZ4085'),
];

describe('CalloutGroup', () => {
  it('says the shared explanation once, not once per occurrence', () => {
    render(<CalloutGroup items={THREE} callout={asCallout} />);
    expect(screen.getAllByText('A VIN names one physical truck.')).toHaveLength(1);
    expect(screen.getAllByText(/Mileage · Maintenance/)).toHaveLength(1);
    // ...and the title, which is also the kind's, is not repeated.
    expect(screen.getAllByText(/is this a different truck/)).toHaveLength(1);
  });

  it('keeps every occurrence answerable', () => {
    render(
      <CalloutGroup
        items={THREE} callout={asCallout}
        actions={(t) => <button type="button">Answer {t.unit}</button>}
      />,
    );
    for (const t of THREE) {
      expect(screen.getByText(`Answer ${t.unit}`)).toBeTruthy();
      expect(screen.getByText(t.unit)).toBeTruthy();
      expect(screen.getByText(t.vins)).toBeTruthy();
    }
    expect(screen.getByText('3')).toBeTruthy();
  });

  it('labels the varying lines once, as a column head', () => {
    render(<CalloutGroup items={THREE} callout={asCallout} />);
    // Three rows, but one "Where" and one "Changed" on the page.
    expect(screen.getAllByText('Where')).toHaveLength(1);
    expect(screen.getAllByText('Changed')).toHaveLength(1);
  });

  it('degrades to a single statement when there is one occurrence', () => {
    render(<CalloutGroup items={[THREE[0]]} callout={asCallout} />);
    // Nothing VARIES across one occurrence, so every line is shared and
    // the group renders exactly the strip it replaced — labelled lines,
    // no column heads, no count.
    const where = screen.getByText('Where');
    expect(where.tagName).toBe('DT');
    expect(screen.queryByText('1')).toBeNull();
  });

  it('folds a long queue behind a button that counts what it hid', () => {
    const many = Array.from({ length: 9 }, (_, i) =>
      truck(`unit${i}`, `OLD${i} → NEW${i}`));
    render(<CalloutGroup items={many} callout={asCallout} />);
    expect(screen.queryByText('unit8')).toBeNull();
    // The count is the FULL total — a fold that under-reports is the
    // silent truncation this is meant to avoid.
    fireEvent.click(screen.getByRole('button', { name: 'Show all 9' }));
    expect(screen.getByText('unit8')).toBeTruthy();
  });

  it('treats a line one occurrence answers and another does not as varying', () => {
    // A truck's own page passes an empty unit; mixing that with a named
    // one must not silently hide either.
    render(<CalloutGroup
      items={[truck('', 'A → B'), truck('254', 'C → D')]}
      callout={asCallout}
    />);
    const heads = screen.getAllByText('Where');
    expect(heads).toHaveLength(1);
    expect(heads[0].tagName).not.toBe('DT');
    expect(screen.getByText('254')).toBeTruthy();
  });

  it('renders nothing for an empty group', () => {
    const { container } = render(<CalloutGroup items={[]} callout={asCallout} />);
    expect(container.firstChild).toBeNull();
  });
});

describe('the answers stay legible', () => {
  it('sets the changed column in mono so VINs can be compared', () => {
    render(<CalloutGroup items={THREE} callout={asCallout} />);
    const cell = screen.getByText(THREE[0].vins);
    expect(cell.className).toContain('font-mono');
    // The unit beside it is a NAME, not a machine identifier — the
    // design system reserves mono for the second.
    expect(screen.getByText('229').className).not.toContain('font-mono');
  });
});

describe('the answer never depends on what varies', () => {
  // Shipped broken: the actions lived inside the varying-columns grid,
  // so a group whose occurrences differ in NOTHING rendered no buttons
  // at all.  A lone gateway swap read "Confirm below if the swap was
  // planned" with nothing below it — an unanswerable question, which is
  // the one state this whole lane exists to prevent.
  it('gives a single occurrence its buttons, with no column to vary', () => {
    render(
      <CalloutGroup
        items={[THREE[0]]} callout={asCallout}
        actions={() => <button type="button">Dismiss</button>}
      />,
    );
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeTruthy();
    // ...and still no column heads, because nothing varies.
    expect(screen.getByText('Where').tagName).toBe('DT');
  });

  it('gives every occurrence its own buttons when several vary', () => {
    render(
      <CalloutGroup
        items={THREE} callout={asCallout}
        actions={(t) => <button type="button">Answer {t.unit}</button>}
      />,
    );
    expect(screen.getAllByRole('button', { name: /^Answer / })).toHaveLength(3);
  });
});

/**
 * The fold, which a group must not lose by being a group.
 *
 * `Callout` has always had it; `CalloutGroup` shipped without ever
 * calling useDismissal, so a collapsible callout would have been
 * collapsible alone and stuck open the moment a second truck developed
 * the same condition.  A control that depends on how many trucks
 * happen to be affected today is not a control.
 *
 * These use the REAL hook against a stubbed preference store, because
 * the interesting behaviour is which ids get written and when the group
 * counts as folded — a mocked hook would assert nothing.
 */
describe('folding a group', () => {
  it('marks every occurrence, so a new truck re-opens the group', async () => {
    prefs.value = {};
    const ids = ['a', 'b', 'c'];
    const { result, rerender } = renderHook(
      ({ list }) => useGroupDismissal('vehicle.no_engine_data', list),
      { initialProps: { list: ids } },
    );
    expect(result.current.collapsed).toBe(false);
    await act(async () => { await result.current.close(); });
    rerender({ list: ids });
    expect(result.current.collapsed).toBe(true);
    expect(Object.keys(prefs.value).sort()).toEqual(['a', 'b', 'c']);

    // The fourth truck arrives with an id nobody folded.  The group must
    // re-open rather than inherit a decision made about the other three
    // — otherwise the fold is a mute button for a condition that is
    // still spreading.
    rerender({ list: [...ids, 'd'] });
    expect(result.current.collapsed).toBe(false);
  });

  it('expanding clears only this group', () => {
    prefs.value = { a: 1, b: 1, other: 1 };
    const { result, rerender } = renderHook(
      () => useGroupDismissal('vehicle.no_engine_data', ['a', 'b']),
    );
    expect(result.current.collapsed).toBe(true);
    act(() => { result.current.expand(); });
    rerender();
    expect(Object.keys(prefs.value)).toEqual(['other']);
  });

  it('refuses to fold a callout that declares no control', async () => {
    prefs.value = {};
    // An unknown key resolves to 'none' — offering to fold it would
    // store an id nothing can ever clear.
    const { result } = renderHook(
      () => useGroupDismissal('nope.nothing', ['a']),
    );
    expect(result.current.behaviour).toBe('none');
    await act(async () => {
      expect(await result.current.close()).toBe(false);
    });
    expect(prefs.value).toEqual({});
  });

  it('renders the control — the component must CALL the hook', () => {
    // The hook tests above would all pass with CalloutGroup never
    // touching it, which is precisely how the deleted dismissal
    // endpoint stayed broken and green.  This one goes through the
    // rendered component.
    prefs.value = {};
    const foldable = (t: { unit: string; vins: string }) => ({
      key: 'vehicle.no_engine_data',
      callout_id: `vehicle.no_engine_data@vehicle:${t.unit}`,
      params: { unit: t.unit, vins: t.vins },
    });
    render(<CalloutGroup items={THREE} callout={foldable} />);
    const fold = screen.getByRole('button', { name: 'callout.labels.collapse' });
    fireEvent.click(fold);
    expect(Object.keys(prefs.value)).toHaveLength(3);
    cleanup();

    // Folded, it is still a statement on screen — and still says how
    // many trucks, which is the number a fold must not swallow.
    render(<CalloutGroup items={THREE} callout={foldable} />);
    expect(screen.queryByText(/A VIN names one physical truck/)).toBeNull();
    expect(screen.getByText('3')).toBeTruthy();
  });

  it('a group of one folds exactly as the single strip does', async () => {
    // useDismissal delegates to useGroupDismissal so the two cannot
    // drift — a strip that folds alone must fold the same way once it
    // has company.
    prefs.value = {};
    const { result } = renderHook(
      () => useDismissal({ key: 'vehicle.no_engine_data', callout_id: 'solo' }),
    );
    await act(async () => { await result.current.close(); });
    expect(prefs.value).toEqual({ solo: expect.any(Number) });
  });
});
