/**
 * Document file → attachable {name, content, kind} parts, ON THE DEVICE.
 *
 * The unified dispatcher behind "Attach document": spreadsheets
 * (CSV/Excel) become CSV text of kind 'sheet' (eligible for the import
 * pipeline), PDFs and plain text become extracted text of kind 'text'
 * (the read-only lane the AI reads through bounded windows).  The wire
 * stays derived text either way — the FILE never leaves the device
 * (capabilities/ai/docs/ai-import-assistant.md §3, owner directive).
 *
 * PDF extraction uses Mozilla's pdf.js, lazy-loaded like SheetJS so the
 * chat bundle carries neither until a matching file is attached.
 */

import { fileToCsvTexts, isSpreadsheetFile } from './spreadsheet';

export type AttachmentKind = 'sheet' | 'text' | 'image';

export interface AttachmentPart {
  name: string;
  content: string;
  kind: AttachmentKind;
}

/** What the picker offers and drag-drop accepts. */
export const DOCUMENT_ACCEPT = [
  '.csv', '.xlsx', '.xlsm', '.xls', '.pdf', '.txt',
  '.png', '.jpg', '.jpeg', '.webp', '.gif',
  'text/csv', 'text/plain', 'application/pdf',
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'image/png', 'image/jpeg', 'image/webp', 'image/gif',
].join(',');

const PDF_RE = /\.pdf$/i;
const TXT_RE = /\.txt$/i;
const IMAGE_RE = /\.(png|jpe?g|webp|gif)$/i;

export function isImageFile(file: File): boolean {
  return IMAGE_RE.test(file.name) || /^image\/(png|jpeg|webp|gif)$/.test(file.type);
}

export function isDocumentFile(file: File): boolean {
  return (
    isSpreadsheetFile(file)
    || PDF_RE.test(file.name)
    || TXT_RE.test(file.name)
    || file.type === 'application/pdf'
    || file.type === 'text/plain'
    || isImageFile(file)
  );
}

// Vision models cap useful resolution around ~1.5k px on the long edge;
// larger only burns tokens.  Re-encoding also strips EXIF (GPS etc.).
const IMAGE_MAX_DIM = 1568;

/** Image file → compressed data-URL (device-side downscale + re-encode). */
export async function imageToDataUrl(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(1, IMAGE_MAX_DIM / Math.max(bitmap.width, bitmap.height));
    const w = Math.max(1, Math.round(bitmap.width * scale));
    const h = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('no 2d context');
    ctx.drawImage(bitmap, 0, 0, w, h);
    // WebP keeps screenshots' text crisp at good ratios; browsers that
    // can't encode it fall back to PNG output — detect and use JPEG then.
    const webp = canvas.toDataURL('image/webp', 0.9);
    return webp.startsWith('data:image/webp')
      ? webp
      : canvas.toDataURL('image/jpeg', 0.85);
  } finally {
    bitmap.close();
  }
}

/** Low-level: PDF bytes → one text string with page markers.
 *  Exported separately so tests can exercise it without File objects. */
export async function pdfToText(data: ArrayBuffer): Promise<string> {
  const pdfjs = await import('pdfjs-dist');
  if (!pdfjs.GlobalWorkerOptions.workerPort && !pdfjs.GlobalWorkerOptions.workerSrc) {
    // `?worker` makes Vite bundle the worker as a plain .js chunk and
    // hands us a Worker constructor.  The `?url` + workerSrc approach
    // emitted a module-script .mjs asset, which breaks behind servers
    // whose MIME table doesn't know .mjs (nginx default) — Chrome then
    // refuses the module worker with a strict-MIME error.
    const PdfWorker = (await import('pdfjs-dist/build/pdf.worker.min.mjs?worker')).default;
    pdfjs.GlobalWorkerOptions.workerPort = new PdfWorker();
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
/** First free screenshot-N name against what's already in THIS chat's
 *  context — numbering belongs to the chat, not to a session counter
 *  (a page-wide counter kept climbing for files that were never sent). */
export function nextScreenshotName(existingNames: string[]): string {
  let max = 0;
  for (const n of existingNames) {
    const m = /^screenshot-(\d+)\.png$/i.exec(n);
    if (m) max = Math.max(max, parseInt(m[1], 10));
  }
  return `screenshot-${max + 1}.png`;
}

export async function fileToAttachmentParts(
  file: File, existingNames: string[] = [],
): Promise<AttachmentPart[]> {
  if (isImageFile(file)) {
    // Pasted screenshots are nameless (or all "image.png") — number them
    // per-chat so a second paste doesn't replace the first chip.
    const name = file.name && file.name !== 'image.png'
      ? file.name
      : nextScreenshotName(existingNames);
    return [{ name, content: await imageToDataUrl(file), kind: 'image' }];
  }
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
