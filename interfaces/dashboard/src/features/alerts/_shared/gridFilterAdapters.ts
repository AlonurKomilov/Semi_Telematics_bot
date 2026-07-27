/**
 * Alerts board: grid filter state ⇄ URL filter state.
 *
 * The board's Type and Severity controls live in the grid's column menus
 * but narrow on the SERVER — the board holds a capped page of a much
 * larger queue, so a client-side narrowing would filter the loaded rows
 * and report that as the answer for all of them.  These adapters are the
 * seam: the grid reports what was ticked, the URL carries it, the query
 * sends it.
 *
 * ``'all'`` is the sentinel for "no filter on this dimension".  It never
 * appears as a VALUE — a grid filter of `['all']` would render an "all"
 * chip and match no row.
 */
import type { ColumnFiltersState } from '@tanstack/react-table';

/** Which column ids this page pushes into the server query. */
const TYPE_ID = 'alert_type';
const SEVERITY_ID = 'severity';

export function toGridFilters(
  typeFilter: string,
  severityFilter: string,
): ColumnFiltersState {
  const out: ColumnFiltersState = [];
  if (typeFilter && typeFilter !== 'all') {
    out.push({ id: TYPE_ID, value: typeFilter.split(',') });
  }
  if (severityFilter && severityFilter !== 'all') {
    out.push({ id: SEVERITY_ID, value: severityFilter.split(',') });
  }
  return out;
}

export function fromGridFilters(next: ColumnFiltersState): {
  typeFilter: string;
  severityFilter: string;
} {
  const read = (id: string) => {
    const hit = next.find((f) => f.id === id);
    const values = Array.isArray(hit?.value) ? (hit.value as string[]) : [];
    // An emptied selection widens the board.  Returning '' here would
    // send an empty filter param and blank the table instead.
    return values.length ? values.join(',') : 'all';
  };
  return { typeFilter: read(TYPE_ID), severityFilter: read(SEVERITY_ID) };
}
