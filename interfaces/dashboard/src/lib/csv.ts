/**
 * CSV export helper for DataTable.
 *
 * Builds an RFC 4180-ish CSV: comma separator, CRLF line ending,
 * double-quote escape for fields containing comma / quote / newline.
 * Triggers a download in the browser via an object URL — no server
 * round-trip, the export is whatever's currently rendered on screen
 * (so filters / sort / hidden columns are honoured).
 */

import type { AnyColumn } from '../types';

/** Quote a CSV field if it contains a special character.  Always
 *  quote when in doubt — over-quoting is benign, under-quoting
 *  breaks Excel and a bunch of CSV parsers.  Embedded quotes are
 *  doubled per the spec. */
function csvField(value: unknown): string {
  if (value == null) return '';
  const s = String(value);
  if (/[",\r\n]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

/** Resolve a column's CSV value for a given row.  Preference order:
 *  ``csvValue`` (explicit opt-in) → ``filterLabel`` (display label
 *  for badge columns: "Critical" not the colour code) →
 *  ``filterValue`` (match value) → raw cell value at ``key``.  This
 *  means columns that already opted into filtering get readable
 *  CSV output for free. */
function cellValue(col: AnyColumn, row: Record<string, unknown>): string {
  if (col.csvValue) return col.csvValue(row);
  if (col.filterLabel) return col.filterLabel(row);
  if (col.filterValue) return col.filterValue(row);
  const v = row[col.key];
  if (v == null) return '';
  // Render dates as ISO so downstream tools (Excel, Sheets) parse
  // them.  Anything more elaborate (locale formats) should be done
  // via ``csvValue``.
  if (v instanceof Date) return v.toISOString();
  return String(v);
}

/** Build the CSV body — header row + one data row per ``rows`` entry.
 *  ``columns`` should already be filtered to the visible/ordered
 *  subset (DataTable does this before calling). */
export function buildCsv(
  columns: AnyColumn[],
  rows: Record<string, unknown>[],
): string {
  const header = columns.map(c => csvField(c.label)).join(',');
  const body = rows
    .map(row => columns.map(c => csvField(cellValue(c, row))).join(','))
    .join('\r\n');
  // Excel reads BOM + UTF-8 as UTF-8 (without the BOM it guesses
  // and often gets it wrong for non-ASCII names).  Tiny cost, big
  // win for accented driver names / Cyrillic / Spanish.
  return '﻿' + header + '\r\n' + body + '\r\n';
}

/** Trigger a browser download of the given CSV text.  Filename
 *  should end in ``.csv``.  Cleans up the object URL after the
 *  click so we don't leak memory across multiple exports. */
export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Defer revoke so the click event has time to start the download
  // before the URL is invalidated.  100ms is plenty in practice.
  setTimeout(() => URL.revokeObjectURL(url), 100);
}

/** Convenience: build + download in one call.  Used by DataTable's
 *  "Export CSV" toolbar button. */
export function exportRowsAsCsv(
  filename: string,
  columns: AnyColumn[],
  rows: Record<string, unknown>[],
): void {
  downloadCsv(filename, buildCsv(columns, rows));
}
