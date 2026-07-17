/**
 * Spreadsheet file → CSV text, entirely ON THE DEVICE.
 *
 * The import pipeline's wire contract is CSV text riding inline on the
 * chat request (docs/architecture/ai-import-assistant.md §3) — that
 * contract doesn't change for Excel support.  Instead the browser
 * converts .xlsx/.xls workbooks to CSV here (SheetJS, lazy-loaded so
 * the chat bundle doesn't carry it), which keeps the owner directive
 * intact: the FILE never leaves the device; only derived text travels.
 *
 * Multi-sheet workbooks become one attachment per non-empty sheet
 * ("Fleet sheet.xlsx — Trucks"), subject to the store's normal caps.
 */

/** What the picker offers and drag-drop accepts. */
export const SPREADSHEET_ACCEPT = [
  '.csv', '.xlsx', '.xlsm', '.xls',
  'text/csv',
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
].join(',');

const EXCEL_RE = /\.(xlsx|xlsm|xls)$/i;
const CSV_RE = /\.csv$/i;

export function isSpreadsheetFile(file: File): boolean {
  return (
    CSV_RE.test(file.name)
    || EXCEL_RE.test(file.name)
    || file.type === 'text/csv'
    || file.type === 'application/vnd.ms-excel'
    || file.type === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
  );
}

/** Low-level: workbook bytes → one CSV text per non-empty sheet.
 *  Exported separately so tests can exercise it without File objects. */
export async function workbookToCsvTexts(
  fileName: string, data: ArrayBuffer,
): Promise<{ name: string; content: string }[]> {
  // Lazy import — SheetJS is ~large and only import users pay for it.
  const XLSX = await import('xlsx');
  const wb = XLSX.read(data, { type: 'array' });
  const parts: { name: string; content: string }[] = [];
  for (const sheetName of wb.SheetNames) {
    const csv = XLSX.utils.sheet_to_csv(wb.Sheets[sheetName]);
    if (!csv.trim()) continue;   // skip empty tabs
    parts.push({ name: sheetName, content: csv });
  }
  // Single-sheet workbooks keep the file's own name; multi-sheet ones
  // are disambiguated per tab.
  return parts.map((p) => ({
    name: parts.length === 1 ? fileName : `${fileName} — ${p.name}`,
    content: p.content,
  }));
}

/**
 * A picked/dropped file → attachable {name, content} parts.
 * CSV passes through as-is; Excel converts per sheet.
 * Throws on unreadable/corrupt files — callers show the friendly error.
 */
export async function fileToCsvTexts(
  file: File,
): Promise<{ name: string; content: string }[]> {
  if (EXCEL_RE.test(file.name)) {
    return workbookToCsvTexts(file.name, await file.arrayBuffer());
  }
  return [{ name: file.name, content: await file.text() }];
}
