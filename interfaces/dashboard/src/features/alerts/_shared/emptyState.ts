/**
 * When may the Alerts board replace its grid with a page-level empty
 * state — and when must the grid stay?
 *
 * The board's segments are CONTROLLED: the server slices them, so the
 * rows in hand are ONE tab under one set of filters. An empty page
 * therefore means "this slice is empty", never "there is nothing".
 *
 * That distinction is not academic. The tab strip, the filter chips and
 * the search box all live INSIDE the grid, so swapping the grid for an
 * empty state deletes the controls that lead OUT of an empty slice.
 * Clicking "Mechanical health 2" in the Alert volume card did exactly
 * that on the live board: New held 0 (both rows already seen), the grid
 * unmounted, and "All 2" became unreachable without a reload — the dead
 * end components/datagrid/CLAUDE.md warns about, reached in practice.
 *
 * So the page-level state is reserved for the only case it describes
 * truthfully: nothing here, nothing anywhere else, and nothing narrowing
 * the view. Everything else keeps the grid and speaks through its
 * ``emptyMessage``.
 */

export interface BoardEmptyInput {
  /** Rows in hand for the current slice. */
  rowCount: number;
  /** A type / severity / vehicle filter is active. */
  narrowed: boolean;
  /** A saved tab is applied. */
  savedTab: string;
  /** The segment currently shown. */
  segment: string;
  /** Server-side per-segment counts; undefined while they load. */
  segmentCounts?: Record<string, number>;
}

/**
 * True only when a full-page empty state is the honest thing to show.
 *
 * Deliberately conservative about UNKNOWNS: while the counts are still
 * loading we cannot prove the other tabs are empty, so we keep the grid.
 * Being wrong that way costs an empty table for one render; being wrong
 * the other way strands the operator.
 */
export function isBoardTrulyEmpty(input: BoardEmptyInput): boolean {
  const { rowCount, narrowed, savedTab, segment, segmentCounts } = input;
  if (rowCount > 0) return false;
  if (narrowed || savedTab) return false;
  // No counts yet — the other tabs are UNKNOWN, not empty.  Treating
  // unknown as empty is what would strand the operator, so it waits.
  if (!segmentCounts) return false;
  const otherTabsHaveRows = Object.entries(segmentCounts)
    .some(([key, n]) => key !== segment && (n ?? 0) > 0);
  return !otherTabsHaveRows;
}
