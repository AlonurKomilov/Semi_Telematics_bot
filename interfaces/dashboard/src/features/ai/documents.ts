/**
 * Document file → attachable {name, content, kind} parts, ON THE DEVICE.
 *
 * The unified dispatcher behind "Attach document": spreadsheets
 * (CSV/Excel) become CSV text of kind 'sheet' (eligible for the import
 * pipeline), PDFs and plain text become extracted text of kind 'text'
 * (the read-only lane the AI reads through bounded windows).  The wire
 * stays derived text either way — the FILE never leaves the device
 * (docs/architecture/ai-import-assistant.md §3, owner directive).
 *
 * PDF extraction uses Mozilla's pdf.js, lazy-loaded like SheetJS so the
 * chat bundle carries neither until a matching file is attached.
 */

import { fileToCsvTexts, isSpreadsheetFile } from './spreadsheet';

export type AttachmentKind = 'sheet' | 'text';

export interface AttachmentPart {
  name: string;
  content: string;
  kind: AttachmentKind;
}

/** What the picker offers and drag-drop accepts. */
export const DOCUMENT_ACCEPT = [
  '.csv', '.xlsx', '.xlsm', '.xls', '.pdf', '.txt',
  'text/csv', 'text/plain', 'application/pdf',
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
].join(',');

const PDF_RE = /\.pdf$/i;
const TXT_RE = /\.txt$/i;

export function isDocumentFile(file: File): boolean {
  return (
    isSpreadsheetFile(file)
    || PDF_RE.test(file.name)
    || TXT_RE.test(file.name)
    || file.type === 'application/pdf'
    || file.type === 'text/plain'
  );
}

/** Low-level: PDF bytes → one text string with page markers.
 *  Exported separately so tests can exercise it without File objects. */
export async function pdfToText(data: ArrayBuffer): Promise<string> {
  const pdfjs = await import('pdfjs-dist');
  if (!pdfjs.GlobalWorkerOptions.workerSrc) {
    const worker = await import('pdfjs-dist/build/pdf.worker.min.mjs?url');
    pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
  }
  const doc = await pdfjs.getDocument({ data }).promise;
  const pages: string[] = [];
  try {
    for (let p = 1; p <= doc.numPages; p++) {
      const page = await doc.getPage(p);
      const tc = await page.getTextContent();
      const text = tc.items
        .map((it) => ('str' in it ? it.str : ''))
        .join(' ')
        .replace(/\s+/g, ' ')
        .trim();
      if (text) pages.push(`[Page ${p}]\n${text}`);
    }
  } finally {
    await doc.destroy();
  }
  return pages.join('\n\n');
}

/**
 * A picked/dropped file → attachable parts.  Throws on unreadable or
 * corrupt files; returns [] when a PDF has no extractable text (scanned
 * images) — callers show the friendly error either way.
 */
export async function fileToAttachmentParts(file: File): Promise<AttachmentPart[]> {
  if (PDF_RE.test(file.name) || file.type === 'application/pdf') {
    const text = await pdfToText(await file.arrayBuffer());
    return text ? [{ name: file.name, content: text, kind: 'text' }] : [];
  }
  // Spreadsheets BEFORE the txt fallback: Windows often stamps .csv
  // files as text/plain, and those must stay in the importable lane.
  if (isSpreadsheetFile(file)) {
    return (await fileToCsvTexts(file)).map((p) => ({ ...p, kind: 'sheet' as const }));
  }
  if (TXT_RE.test(file.name) || file.type === 'text/plain') {
    return [{ name: file.name, content: await file.text(), kind: 'text' }];
  }
  return [];
}
