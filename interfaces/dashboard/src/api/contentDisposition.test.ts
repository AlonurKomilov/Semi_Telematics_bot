/**
 * The download name comes from the server, or from nowhere.
 *
 * The bug this closes: the extension card fetched the zip with a bearer
 * token, turned it into a blob, and then named the file itself with a
 * constant.  `a.download` beats `Content-Disposition` absolutely, so a
 * server that had been fixed to stamp the version into the name was
 * overridden on every single download — the owner's Downloads folder
 * filled with `4truck-extension (8)`, `(9)`, `(10)`, all different
 * builds, none distinguishable.
 */
import { describe, expect, it } from 'vitest';

import { filenameFromDisposition } from './contentDisposition';
import cardSrc from '../features/profile/BrowserExtensionCard.tsx?raw';

describe('filenameFromDisposition', () => {
  it('reads the name our own API sends', () => {
    expect(filenameFromDisposition(
      'attachment; filename="4truck-extension-sideload-0.5.1.0.zip"',
    )).toBe('4truck-extension-sideload-0.5.1.0.zip');
  });

  it('accepts an unquoted name and odd spacing', () => {
    expect(filenameFromDisposition('attachment; filename=report.csv')).toBe('report.csv');
    expect(filenameFromDisposition('attachment;filename = report.csv')).toBe('report.csv');
    expect(filenameFromDisposition('ATTACHMENT; FILENAME="a.zip"')).toBe('a.zip');
  });

  it("prefers filename* — that is what it is for", () => {
    // The ASCII `filename` is the fallback for clients that cannot read
    // the extended form; a client that CAN must not take the lossy one.
    expect(filenameFromDisposition(
      `attachment; filename="report.csv"; filename*=UTF-8''hisobot%20yakuniy.csv`,
    )).toBe('hisobot yakuniy.csv');
  });

  it('falls back to the plain name when the extended one is unusable', () => {
    expect(filenameFromDisposition(
      `attachment; filename="good.zip"; filename*=UTF-8''`,
    )).toBe('good.zip');
  });

  it('returns a NAME, never a path', () => {
    // The header is remote input.  Separators have no meaning in a save
    // dialog, and a caller must never be handed something traversal-ish.
    expect(filenameFromDisposition('attachment; filename="../../etc/passwd"')).toBe('passwd');
    expect(filenameFromDisposition('attachment; filename="C:\\\\Windows\\\\x.dll"')).toBe('x.dll');
    expect(filenameFromDisposition('attachment; filename=".."')).toBeNull();
  });

  it('answers null when the server said nothing usable', () => {
    // Null is a REAL answer, not an error: Content-Disposition is not
    // CORS-safelisted, so a cross-origin API without expose_headers
    // gives the client nothing to read.  Callers keep a fallback.
    expect(filenameFromDisposition(null)).toBeNull();
    expect(filenameFromDisposition(undefined)).toBeNull();
    expect(filenameFromDisposition('')).toBeNull();
    expect(filenameFromDisposition('attachment')).toBeNull();
    expect(filenameFromDisposition('attachment; filename=""')).toBeNull();
  });
});

describe('the extension card', () => {
  const card = cardSrc as unknown as string;

  it('asks the server for the name instead of inventing one', () => {
    // The whole defect in one line: `a.download = '4truck-extension.zip'`
    // sat here and beat a correct server header on every download.
    expect(card).toContain("filenameFromDisposition(res.headers.get('content-disposition'))");
    expect(card).not.toMatch(/a\.download\s*=\s*'4truck-extension\.zip'/);
  });

  it('still has a fallback, because the header can be unreadable', () => {
    expect(card).toContain("?? '4truck-extension.zip'");
  });
});
