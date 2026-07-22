// ── Saved Views — user-managed scope tabs ───────────────────────────
//
// A "view" is a per-user tab an operator builds from the grid's OWN
// filter state — Category is Camera, Priority is Critical.  It applies
// as an isolated SCOPE (the grid's dataset is replaced with the matching
// subset, exactly like the code-defined Active/Archive segments), NOT as
// a removable filter — so nothing leaks between tabs and sort / export /
// select-all stay inside the tab.
//
// This module is framework-free and unit-tested: the matching a view
// does is the SAME logic the live column filters use (``rowPassesColFilter``
// lives here and is imported back by the grid + its faceted-options
// computation), so a saved view can never scope differently than the
// filters it was captured from.

import type { ColumnFiltersState } from '@tanstack/react-table';
import type { AnyColumn } from '../../types';

export interface SavedView {
  /** Stable id (also the segment key, prefixed) — kept out of the label
   *  so a rename never breaks the active-tab reference. */
  id: string;
  name: string;
  /** The captured column filters — the view's scope definition. */
  filters: ColumnFiltersState;
  /** The captured global search, if any. */
  search?: string;
  /** The built-in segment key active when the view was saved (if any).
   *  A view captured while on "Active" composes WITH that segment's
   *  scope — so "Critical" made on Active shows active criticals, not
   *  archived ones too.  Undefined on grids with no segments (or when
   *  saved from the implicit "All" tab). */
  baseSegment?: string;
}

/**
 * Does a RAW data row pass one column's active filter value?  Mirrors
 * the tanstack ``filterFn`` shapes (select / range / date-range) but
 * works on the plain original row — used by the live grid's faceted
 * options AND by view scoping, so the two can never diverge.
 */
export function rowPassesColFilter(
  row: Record<string, unknown>,
  col: AnyColumn,
  fv: unknown,
): boolean {
  if (col.filterMode === 'range') {
    const range = fv as [number | null, number | null] | undefined;
    if (!range || (range[0] == null && range[1] == null)) return true;
    const raw = row[col.key];
    const n = typeof raw === 'number' ? raw : Number(raw);
    if (!Number.isFinite(n)) return false;
    if (range[0] != null && n < range[0]) return false;
    if (range[1] != null && n > range[1]) return false;
    return true;
  }
  if (col.filterMode === 'date-range') {
    const range = fv as [string | null, string | null] | undefined;
    if (!range || (!range[0] && !range[1])) return true;
    const t = new Date(String(row[col.key] ?? '')).getTime();
    if (!Number.isFinite(t)) return false;
    if (range[0]) {
      const fromT = new Date(range[0]).getTime();
      if (Number.isFinite(fromT) && t < fromT) return false;
    }
    if (range[1]) {
      const toT = new Date(range[1] + 'T23:59:59.999').getTime();
      if (Number.isFinite(toT) && t > toT) return false;
    }
    return true;
  }
  const selected = fv as string[] | undefined;
  if (!selected || selected.length === 0) return true;
  const hay = col.filterValue
    ? col.filterValue(row)
    : String(row[col.key] ?? '');
  return selected.includes(hay);
}

/** Global-search match on a raw row — the same "any searchKey contains
 *  the needle" rule the grid's global filter uses.  ``needle`` must be
 *  pre-lowercased + trimmed. */
export function rowMatchesSearch(
  row: Record<string, unknown>,
  searchKeys: string[],
  needle: string,
): boolean {
  if (!needle) return true;
  return searchKeys.some(k => {
    const v = row[k];
    return v ? String(v).toLowerCase().includes(needle) : false;
  });
}

/**
 * Build the scope predicate for a view — the ``match`` a segment uses.
 * A row is in the view when it passes EVERY captured column filter AND
 * the captured search.  Filters referencing a column that no longer
 * exists are ignored (a stale view still scopes on its live criteria).
 */
export function viewMatch(
  view: SavedView,
  columns: AnyColumn[],
  searchKeys: string[],
): (row: Record<string, unknown>) => boolean {
  const byKey = new Map(columns.map(c => [c.key, c]));
  const needle = (view.search ?? '').trim().toLowerCase();
  return (row) => {
    for (const f of view.filters) {
      const col = byKey.get(f.id);
      if (col && !rowPassesColFilter(row, col, f.value)) return false;
    }
    return rowMatchesSearch(row, searchKeys, needle);
  };
}

/** Does a view actually constrain anything?  An empty view (no filters,
 *  no search) would scope to "everything" — we block saving those. */
export function viewIsEmpty(filters: ColumnFiltersState, search: string): boolean {
  return filters.length === 0 && search.trim() === '';
}
