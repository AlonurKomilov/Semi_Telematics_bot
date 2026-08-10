/**
 * One close button per sheet.
 *
 * `SheetContent` renders its own ✕ by default (`showCloseButton = true`),
 * which is right for a drawer with no header chrome of its own. But a
 * modal being CONVERTED from a hand-rolled overlay always arrives with a
 * ✕ already in its header — so the two land on top of each other, a few
 * pixels apart, and one of them is usually unlabelled.
 *
 * This has happened five times: TripsDrawer, IncidentDrillInDrawer, the
 * Applications detail sheet, TaskDetailSheet, and Scorecards — the last
 * one sat there unnoticed until a screenshot caught the doubled glyph.
 * Every instance was invisible to the type-checker and to every test.
 *
 * Flipping the default was considered and rejected: three sheets
 * (ParkingDetailSheet, Cameras, Companies) legitimately rely on it and
 * have no ✕ of their own. The default is right; forgetting to override
 * it is what needs catching.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(__dirname, '..', '..');

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (/\.tsx$/.test(name) && !/\.test\.tsx$/.test(name)) out.push(path);
  }
  return out;
}

/** The span of the first `<SheetContent …>` … `</SheetContent>`. */
function sheetBody(src: string): string | null {
  const open = src.indexOf('<SheetContent');
  if (open === -1) return null;
  const close = src.indexOf('</SheetContent>', open);
  return src.slice(open, close === -1 ? undefined : close);
}

/** A ✕ the file renders itself — `aria-label="Close"`, a translated
 *  close label, or a bare `<X size=…>` glyph. */
const OWN_CLOSE = /aria-label=\{?["']?(Close|t\(['"]common\.close)|<X size=/;

describe('sheets render exactly one close button', () => {
  it('suppresses the built-in ✕ wherever the header already has one', () => {
    const offenders = walk(SRC)
      .filter((f) => !f.endsWith(join('ui', 'sheet.tsx')))
      .filter((f) => {
        const src = readFileSync(f, 'utf8');
        const body = sheetBody(src);
        if (!body) return false;
        return OWN_CLOSE.test(body) && !body.includes('showCloseButton={false}');
      })
      .map((f) => relative(SRC, f));

    // Named, not counted — the fix is one prop, but only if you know
    // which file needs it.
    expect(offenders).toEqual([]);
  });
});
