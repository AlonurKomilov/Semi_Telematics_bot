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
import { render, screen, cleanup, fireEvent } from '@testing-library/react';

afterEach(cleanup);

const LABELS: Record<string, string> = {
  'callout.labels.where': 'Where',
  'callout.labels.changed': 'Changed',
  'callout.labels.why': 'Why',
  'callout.labels.affects': 'Affects',
};

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
