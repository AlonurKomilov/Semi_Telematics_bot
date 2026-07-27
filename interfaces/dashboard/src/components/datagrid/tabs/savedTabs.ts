// ── Saved Tabs — user-managed scope tabs ────────────────────────────
//
// TWO KINDS OF TAB share one strip, and they are NOT the same thing:
//   • Built-in segments (``DataGridSegment[]`` via the ``segments`` prop,
//     e.g. TASK_SEGMENTS Active/Archive) — code-defined, account-wide,
//     the same for every user.  Live in the page files + DataGrid.tsx.
//   • Saved tabs (THIS module, via the ``savedTabs`` prop) — user-defined,
//     per-user, built from the grid's own filter state.  Persisted in
//     user_preferences.  Told apart at runtime by the ``TAB_PREFIX``
//     key prefix / ``isTab`` check in DataGrid.tsx.
//
// A saved tab is one an operator builds from the grid's OWN filter state
// — Category is Camera, Priority is Critical.  It applies as an isolated
// SCOPE (the grid's dataset is replaced with the matching subset, exactly
// like the code-defined Active/Archive segments), NOT as a removable
// filter — so nothing leaks between tabs and sort / export / select-all
// stay inside the tab.
//
// This module is framework-free and unit-tested: the matching a tab does
// is the SAME logic the live column filters use (``rowPassesColFilter``
// lives here and is imported back by the grid + its faceted-options
// computation), so a saved tab can never scope differently than the
// filters it was captured from.

import type { ColumnFiltersState, SortingState } from '@tanstack/react-table';
import type { AnyColumn } from '../../../types';
import type { PivotModel } from '../pivot/pivot';
import type { Tone } from '../../../lib/status';

export interface SavedTab {
  /** Stable id (also the segment key, prefixed) — kept out of the label
   *  so a rename never breaks the active-tab reference. */
  id: string;
  name: string;
  /** The captured column filters — the tab's scope definition. */
  filters: ColumnFiltersState;
  /** The captured global search, if any. */
  search?: string;
  /** The captured sort — applied (as a starting point you can then
   *  change) when the tab is selected.  Omitted when nothing was sorted.
   *  Sort is session state, so a tab can carry its own without touching
   *  the grid's global grouping / column layout. */
  sort?: SortingState;
  /** The built-in segment key active when the tab was saved (if any).
   *  A tab captured while on "Active" composes WITH that segment's
   *  scope — so "Critical" made on Active shows active criticals, not
   *  archived ones too.  Undefined on grids with no segments (or when
   *  saved from the implicit "All" tab). */
  baseSegment?: string;
  /** The pivot configuration this tab opens with, if any.  Optional and
   *  added late on purpose — a tab saved before pivot existed simply has
   *  none, and switching to it leaves the current pivot alone. */
  pivot?: { enabled: boolean; model: PivotModel } | null;
  /** Optional customization (personal recognition — no effect on scope).
   *  ``tone`` colours ONLY the count badge (the number), via toneClasses;
   *  ``icon`` is a leading lucide icon key from tabs/tabIcons. */
  tone?: Tone;
  icon?: string;
}

/**
 * Does a RAW data row pass one column's active filter value?  Mirrors
 * the tanstack ``filterFn`` shapes (select / range / date-range) but
 * works on the plain original row — used by the live grid's faceted
 * options AND by tab scoping, so the two can never diverge.
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
 * Build the scope predicate for a tab — the ``match`` a segment uses.
 * A row is in the tab when it passes EVERY captured column filter AND
 * the captured search.  Filters referencing a column that no longer
 * exists are ignored (a stale tab still scopes on its live criteria).
 */
export function tabMatch(
  tab: SavedTab,
  columns: AnyColumn[],
  searchKeys: string[],
): (row: Record<string, unknown>) => boolean {
  const byKey = new Map(columns.map(c => [c.key, c]));
  const needle = (tab.search ?? '').trim().toLowerCase();
  return (row) => {
    for (const f of tab.filters) {
      const col = byKey.get(f.id);
      if (col && !rowPassesColFilter(row, col, f.value)) return false;
    }
    return rowMatchesSearch(row, searchKeys, needle);
  };
}

/** Does a tab actually constrain anything?  An empty tab (no filters,
 *  no search) would scope to "everything" — we block saving those. */
export function tabIsEmpty(filters: ColumnFiltersState, search: string): boolean {
  return filters.length === 0 && search.trim() === '';
}

/** A column's available filter choices — computed once over the FULL
 *  dataset so the New-tab builder can offer any value / the real bounds
 *  (not just the current scope's).  Shape matches ``ColumnFilterMenu``. */
export interface ColFacet {
  options: { value: string; label: string }[];   // select mode
  counts: Record<string, number>;                 // select mode
  numeric?: { min: number; max: number };         // range mode
  dates?: { min: string; max: string };           // date-range mode
}

/** Compute per-column facets for the filterable columns. */
export function computeFacets(
  columns: AnyColumn[],
  rows: Record<string, unknown>[],
): Record<string, ColFacet> {
  const out: Record<string, ColFacet> = {};
  for (const col of columns) {
    if (!col.filterable) continue;
    if (col.filterMode === 'range') {
      // Honour an author-set ``filterRange.min/max`` (e.g. a 0–100 %),
      // only data-scanning the side left unset — mirrors the grid's own
      // rangesByCol so the dialog's hint matches the header filter.
      const scanMin = col.filterRange?.min == null;
      const scanMax = col.filterRange?.max == null;
      let min = col.filterRange?.min ?? Infinity;
      let max = col.filterRange?.max ?? -Infinity;
      if (scanMin || scanMax) {
        for (const r of rows) {
          const raw = r[col.key];
          if (raw == null) continue;   // null ≠ 0: don't drag the min to 0
          const n = Number(raw);
          if (!Number.isFinite(n)) continue;
          if (scanMin && n < min) min = n;
          if (scanMax && n > max) max = n;
        }
      }
      out[col.key] = {
        options: [], counts: {},
        numeric: {
          min: Number.isFinite(min) ? min : 0,
          max: Number.isFinite(max) ? max : 0,
        },
      };
    } else if (col.filterMode === 'date-range') {
      let min = '';
      let max = '';
      for (const r of rows) {
        const v = r[col.key];
        if (!v) continue;
        const day = String(v).slice(0, 10);
        const t = new Date(day).getTime();
        if (!Number.isFinite(t)) continue;
        if (!min || t < new Date(min).getTime()) min = day;
        if (!max || t > new Date(max).getTime()) max = day;
      }
      out[col.key] = { options: [], counts: {}, dates: { min, max } };
    } else {
      const counts: Record<string, number> = {};
      const labelByValue: Record<string, string> = {};
      for (const r of rows) {
        const v = col.filterValue ? col.filterValue(r) : String(r[col.key] ?? '');
        if (v === '') continue;
        counts[v] = (counts[v] ?? 0) + 1;
        if (!(v in labelByValue)) labelByValue[v] = col.filterLabel ? col.filterLabel(r) : v;
      }
      out[col.key] = {
        options: Object.keys(counts).sort((a, b) =>
          (labelByValue[a] || a).localeCompare(labelByValue[b] || b),
        ).map(v => ({ value: v, label: labelByValue[v] })),
        counts,
      };
    }
  }
  return out;
}

/** Is a single column's captured filter value "empty" (contributes no
 *  constraint)?  Used to strip no-op rows before saving. */
export function isFilterValueEmpty(mode: string | undefined, value: unknown): boolean {
  if (mode === 'range' || mode === 'date-range') {
    const r = value as [unknown, unknown] | undefined;
    return !r || (r[0] == null || r[0] === '') && (r[1] == null || r[1] === '');
  }
  return !Array.isArray(value) || value.length === 0;
}
