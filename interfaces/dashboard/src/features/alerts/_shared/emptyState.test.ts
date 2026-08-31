/**
 * The rule that keeps an empty tab from becoming a dead end.
 *
 * Reported from the live board: clicking "Mechanical health 2" in the
 * Alert volume card left "No alerts match these filters" and NO tab
 * strip — New held 0 because both rows had been seen, and the control
 * that would have reached "All 2" had been unmounted along with the
 * grid it lives inside.
 */
import { describe, expect, it } from 'vitest';
import { isBoardTrulyEmpty } from './emptyState';

const base = {
  rowCount: 0,
  narrowed: false,
  savedTab: '',
  segment: 'new',
  segmentCounts: undefined as Record<string, number> | undefined,
};

describe('isBoardTrulyEmpty', () => {
  it('keeps the grid when another tab still has rows', () => {
    // The reported bug, exactly: New 0 while All holds 2.
    expect(isBoardTrulyEmpty({
      ...base, segmentCounts: { new: 0, all: 2, mine_working: 0 },
    })).toBe(false);
  });

  it('keeps the grid whenever a filter is narrowing the view', () => {
    // The filter chips are inside the grid, so they must survive the
    // filter that emptied the tab — otherwise the only way to widen is
    // a reload.
    expect(isBoardTrulyEmpty({ ...base, narrowed: true })).toBe(false);
  });

  it('keeps the grid while a saved tab is applied', () => {
    expect(isBoardTrulyEmpty({ ...base, savedTab: 't1' })).toBe(false);
  });

  it('keeps the grid while the counts are still unknown', () => {
    // Cannot prove the other tabs are empty yet.  One empty render is
    // cheaper than stranding the operator.
    expect(isBoardTrulyEmpty(base)).toBe(false);
  });

  it('allows the page state when every tab is empty and nothing narrows', () => {
    expect(isBoardTrulyEmpty({
      ...base, segmentCounts: { new: 0, all: 0, mine_working: 0 },
    })).toBe(true);
  });

  it('ignores the CURRENT tab’s own count', () => {
    // A stale count for the tab in hand must not keep the grid alive on
    // its own — the rows are the truth for the slice being rendered.
    expect(isBoardTrulyEmpty({
      ...base, segmentCounts: { new: 7, all: 0, mine_working: 0 },
    })).toBe(true);
  });

  it('never shows the page state while rows are on screen', () => {
    expect(isBoardTrulyEmpty({ ...base, rowCount: 1 })).toBe(false);
  });
});
