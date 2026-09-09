/**
 * Pure helpers for the role lens's reading aids — the band summary, the
 * feature search, the anchor a department switch scrolls to.  No React,
 * so each is a unit test away from proof.
 */
import type { TickRow, VerbFamily } from './verbGrid';

/** Every tickable row a band holds — parents, their Manage rows, children. */
export function bandRows(families: VerbFamily[]): TickRow[] {
  const out: TickRow[] = [];
  for (const fam of families) {
    out.push(fam.parent);
    if (fam.manage) out.push(fam.manage);
    for (const c of fam.children) out.push(c.row);
  }
  return out;
}

/** The rows "Grant all" ticks: every VIEW-verb row (a parent, a merged
 *  row, a view child).  Manage rows are left alone — granting the write
 *  is a decision per feature, never per band. */
export function viewRows(families: VerbFamily[]): TickRow[] {
  const out: TickRow[] = [];
  for (const fam of families) {
    out.push(fam.parent);
    for (const c of fam.children) if (c.verb !== 'manage') out.push(c.row);
  }
  return out;
}

/** "n of m" for a band header, counted over every tickable row. */
export function bandSummary(
  families: VerbFamily[], granted: (row: TickRow) => boolean,
): { granted: number; total: number } {
  const rows = bandRows(families);
  return { granted: rows.filter(granted).length, total: rows.length };
}

const norm = (s: string | undefined): string => (s ?? '').toLowerCase();

/** A family matches when the query appears in its own label or
 *  description, in its MANAGE verb's, or in one of its children's.
 *
 *  The manage row used to be missing from this list while `bandRows`
 *  counted it — so a band could report a row the find box could not
 *  reach, and a search for a verb only the manage row names came back
 *  empty against a matrix that plainly contained it. */
export function familyMatches(fam: VerbFamily, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hit = (r?: { label?: string; description?: string }) =>
    !!r && (norm(r.label).includes(q) || norm(r.description).includes(q));
  if (hit(fam.parent) || hit(fam.manage)) return true;
  return fam.children.some((c) => hit(c.row));
}

/** The element id a band header carries, and a department chip scrolls to. */
export function bandAnchor(title: string): string {
  return `perm-band-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
}
