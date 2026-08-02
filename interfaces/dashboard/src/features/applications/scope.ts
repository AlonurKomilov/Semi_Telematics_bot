// What subset of applications is on screen right now.
//
// This page shows ONE dataset through two surfaces — the table and the
// Board — and the grid narrows its own rows without telling the page.
// So the page derives the same subset independently, and any
// disagreement shows up as the same dataset presented as two different
// populations.  That already happened once: switching Table→Board made
// Withdrawn records reappear.
//
// A saved tab makes the rule non-obvious enough to be worth stating in
// one tested place: a tab is a scope WITHIN the lifecycle slice it was
// captured under, and composes with it.  Mirrors DataGrid's own
// composition in ``tabSegments`` — keep the two in step.
//
// ⚠️ That mirror assumes this page filters CLIENT-side.  DataGrid drops a
// tab's own criteria when ``manualFiltering`` is on (they go to the
// server instead), so adding that prop to this grid without teaching the
// functions below about it re-opens the divergence they exist to close.

import { tabMatch } from '../../components/datagrid';
import type { DataGridSegment } from '../../components/datagrid';
import type { AnyColumn } from '../../types';
import type { ColumnFiltersState } from '@tanstack/react-table';

/** The slice of a saved tab this page needs: enough to REPLAY its scope
 *  (filters + search) and to know which lifecycle slice it was captured
 *  under.  DataGrid hands exactly this through ``onSegmentChange``; the
 *  id comes from the segment key. */
export interface TabScope {
  id: string;
  filters: ColumnFiltersState;
  search: string;
  baseSegment?: string;
}

type Row = Record<string, unknown>;
type Match = (row: Row) => boolean;

/** The lifecycle slice bounding the current scope: a LIVE segment key,
 *  or null meaning unbounded.
 *
 *  Null has one non-obvious source.  A tab saved while already standing
 *  on another tab carries no ``baseSegment``, so it can span active,
 *  hired and closed at once — and a tab whose ``baseSegment`` no longer
 *  exists is the same situation, because DataGrid drops a stale base to
 *  the tab's own filters.  A caller asking "what can these rows do"
 *  (the bulk bar) must read null as "no single answer", not as a slice. */
export function resolveScopeBase(
  segment: string,
  tab: TabScope | null,
  segments: DataGridSegment[],
): string | null {
  const raw = tab ? (tab.baseSegment ?? null) : segment;
  if (!raw) return null;
  return segments.some((sg) => sg.key === raw) ? raw : null;
}

/** The predicate for the rows currently in scope, or undefined for "all
 *  of them".  Undefined rather than ``() => true`` so a caller can skip
 *  the filter pass entirely, which is what the page did before tabs. */
export function scopeMatch(
  scopeBase: string | null,
  tab: TabScope | null,
  segments: DataGridSegment[],
  columns: AnyColumn[],
  searchKeys: string[],
): Match | undefined {
  const base = segments.find((sg) => sg.key === scopeBase)?.match;
  if (!tab) return base;
  const inTab = tabMatch(
    { id: tab.id, name: '', filters: tab.filters, search: tab.search },
    columns, searchKeys,
  );
  return base ? (row) => base(row) && inTab(row) : inTab;
}
