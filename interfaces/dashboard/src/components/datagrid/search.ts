// ── Global search — the rule, and where a hit came from ─────────────
//
// Two functions that MUST agree, which is the whole reason they share a
// file: ``rowMatchesSearch`` decides whether a row is in the result, and
// ``searchProvenance`` explains WHY each surviving row is there.  If the
// second one used a different notion of "this cell's text" than the
// first, the grid would show rows it could not account for and account
// for rows it did not show — the exact confusion this module exists to
// remove.  Both read every column through ``cellText``.
//
// Framework-free and unit-tested.  ``rowMatchesSearch`` lived in
// ``tabs/savedTabs.ts`` (a saved tab captures a search, so it needed
// it); it is re-exported from there so that import keeps working.

import type { AnyColumn } from '../../types';
import { cellText } from '../../lib/cellText';

/** Global-search match on a raw row — THE rule, used by the grid's live
 *  global filter, by a saved tab's captured search, and by the filter
 *  dropdowns' option counts.  ``needle`` must be pre-lowercased +
 *  trimmed.
 *
 *  A row matches when the needle appears in any ``searchKeys`` field OR
 *  in any COLUMN's text.  Both halves earn their place:
 *
 *   * ``columns`` is what makes the box mean "search this table".  A
 *     page that declared 2 keys while rendering 20 columns answered for
 *     a tenth of what the operator could see — Carrier Directory showed
 *     "60" in a Solo Pay Rate cell and "No match for 60" in the same
 *     frame.  Columns are read through ``cellText``, the accessor CSV
 *     export uses, so a badge column matches the word it displays
 *     ("Critical") rather than the code behind it.
 *   * ``searchKeys`` reaches row fields that are NOT columns (a
 *     carrier's ``experience_summary``) — data the operator can't see
 *     but does expect to find.
 *
 *  Hidden columns are searched too: on a 74-field directory, "which
 *  carrier mentioned hazmat?" must not require knowing which column
 *  holds it first.  ``searchProvenance`` below is what keeps that from
 *  costing the operator an unexplained row. */
export function rowMatchesSearch(
  row: Record<string, unknown>,
  searchKeys: string[],
  needle: string,
  columns: AnyColumn[] = [],
): boolean {
  if (!needle) return true;
  for (const k of searchKeys) {
    const v = row[k];
    // Not `v ? …` — that drops a legitimate 0 or false.
    if (v == null || typeof v === 'object') continue;
    if (String(v).toLowerCase().includes(needle)) return true;
  }
  for (const col of columns) {
    const text = cellText(col, row);
    if (text && text.toLowerCase().includes(needle)) return true;
  }
  return false;
}

/** A hidden column that accounts for rows nothing visible accounts for. */
export interface SearchSource {
  key: string;
  label: string;
  /** How many of the scanned rows this column explains. */
  rows: number;
}

export interface SearchProvenance {
  /** Rows whose match NO visible column shows — the unexplained ones. */
  unexplained: number;
  /** Hidden columns that explain at least one of them, most-explaining
   *  first (ties keep column order, so the answer is stable). */
  sources: SearchSource[];
  /** Unexplained rows that no COLUMN explains either — they matched a
   *  ``searchKey`` row field that isn't rendered anywhere.  Revealing a
   *  column cannot help these, so the caller must not offer to. */
  fieldOnly: number;
}

const EMPTY: SearchProvenance = { unexplained: 0, sources: [], fieldOnly: 0 };

/**
 * Why is this row in the result?
 *
 * Searching hidden columns is right — you shouldn't have to know which
 * of 76 columns holds "hazmat" before you can search for it — but it
 * hands the operator a row whose every visible cell reads "—" and no
 * account of itself.  That looks like a bug in the search, and the only
 * recovery is to guess which column to reveal out of 76.
 *
 * So: for the rows the operator is actually looking at, find the ones
 * NOTHING VISIBLE explains, and name the hidden columns that do.
 *
 * Scanning is deliberately scoped to the rows PASSED IN (the caller
 * hands over the current page, not the whole result): the note explains
 * the rows on screen, the cost is bounded by the page size rather than
 * by the dataset, and a claim about rows the operator can't see would be
 * unverifiable anyway.
 *
 * ``needle`` must be pre-lowercased + trimmed, as in ``rowMatchesSearch``.
 */
export function searchProvenance(
  rows: Record<string, unknown>[],
  columns: AnyColumn[],
  isVisible: (key: string) => boolean,
  needle: string,
): SearchProvenance {
  if (!needle || rows.length === 0 || columns.length === 0) return EMPTY;

  const visible: AnyColumn[] = [];
  const hidden: AnyColumn[] = [];
  for (const col of columns) (isVisible(col.key) ? visible : hidden).push(col);
  // Nothing is hidden, so no hit can be hiding.  (A row may still be
  // here on a ``searchKey`` field, but with every column on screen
  // there is no column to offer, and "some invisible field matched" on
  // its own is a nag, not a help.)
  if (hidden.length === 0) return EMPTY;

  const hits = new Map<string, number>();
  let unexplained = 0;
  let fieldOnly = 0;

  for (const row of rows) {
    let seen = false;
    for (const col of visible) {
      const text = cellText(col, row);
      if (text && text.toLowerCase().includes(needle)) { seen = true; break; }
    }
    if (seen) continue;
    unexplained++;
    let explained = false;
    for (const col of hidden) {
      const text = cellText(col, row);
      if (text && text.toLowerCase().includes(needle)) {
        hits.set(col.key, (hits.get(col.key) ?? 0) + 1);
        explained = true;
      }
    }
    if (!explained) fieldOnly++;
  }

  if (unexplained === 0) return EMPTY;

  // Column order is the tiebreak, so the same search always names the
  // same column first — a list that reshuffles between renders reads as
  // two different answers.
  const order = new Map(columns.map((c, i) => [c.key, i]));
  const sources = [...hits.entries()]
    .map(([key, n]) => ({
      key,
      label: columns.find(c => c.key === key)?.label ?? key,
      rows: n,
    }))
    .sort((a, b) => b.rows - a.rows || (order.get(a.key)! - order.get(b.key)!));

  return { unexplained, sources, fieldOnly };
}
