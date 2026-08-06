/**
 * No hand-rolled modal backdrops. Zero, and the build says so.
 *
 * ESLint has flagged `fixed inset-0 … bg-black/…` for a while, but only
 * as a WARNING — one line in a 200-warning pile, which is close to
 * invisible. It could not be raised to an error because it shares
 * `no-restricted-syntax` with the `title=` ban, and that one still has
 * live violations; flipping the rule would break the build for an
 * unrelated migration.
 *
 * So the guard moves here. Eight files carried one of these at the start
 * of this arc — every one missing the same four things (focus trap,
 * Escape, `aria-modal`, background scroll lock), and two of them were
 * outright keyboard traps. They are all `<Sheet>` or `<Dialog>` now, and
 * a ninth must not appear quietly.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(__dirname, '..', '..');

/** `fixed inset-0` anywhere in a className, with a black scrim after it. */
const BACKDROP = /className="[^"]*fixed inset-0[^"]*bg-black\//;

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (/\.tsx$/.test(name) && !/\.test\.tsx$/.test(name)) out.push(path);
  }
  return out;
}

describe('modal backdrops', () => {
  it('are never hand-rolled', () => {
    const offenders = walk(SRC)
      .filter((f) => BACKDROP.test(readFileSync(f, 'utf8')))
      .map((f) => relative(SRC, f));

    // Named, not counted: the failure should say which file to convert.
    // Use <Sheet> for a side drawer, <Dialog> for a centred one. If the
    // backdrop IS the surface (a lightbox), style the CONTENT full-bleed
    // and let the primitive's own overlay sit behind it — MediaGallery is
    // the worked example.
    expect(offenders).toEqual([]);
  });
});
