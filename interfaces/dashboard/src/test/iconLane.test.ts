/**
 * One door to the icon set.
 *
 * `lib/icons` is the only file that may name `lucide-react`. That only
 * holds while nothing goes around it — and a file that does looks
 * exactly like a file that does not: the icon draws, the suite passes,
 * and the only symptom arrives on the day a second pack ships, as a
 * screen with two vocabularies on it.
 *
 * That mixed-set screen is what the design system's "no second icon
 * set" rule has always been about. The door is what turns the rule from
 * a convention into something a build can refuse.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');

/** Who may name the library, and why. `lib/icons.ts` IS the door. */
const ALLOWED = 'lib/icons.ts';

/**
 * And this file, which cannot obey its own rule: the fabricated
 * offenders below are the positive control, so the strings it hunts for
 * are in its source by construction.
 *
 * Only this one file, not every test — a test that reaches the library
 * directly is a file somebody has to edit on a pack swap, exactly like
 * any other.
 */
const SELF = relative(SRC, fileURLToPath(import.meta.url));

function walk(dir: string, acc: string[] = []): string[] {
  for (const e of readdirSync(dir)) {
    const full = join(dir, e);
    if (statSync(full).isDirectory()) { walk(full, acc); continue; }
    if (/\.tsx?$/.test(full)) acc.push(relative(SRC, full));
  }
  return acc;
}

/**
 * Every line naming the library outside the door.
 *
 * Extracted so the detector can be shown to work: `offenders.toEqual([])`
 * is satisfied by a check that skips everything, and there is no
 * offending file in the tree to prove otherwise.
 */
function offendersIn(file: string, src: string): string[] {
  if (file === ALLOWED || file === SELF) return [];
  return [...src.matchAll(/^.*from '(lucide-react)'.*$/gm)]
    .map((m) => `${file}: ${m[0].trim()}`);
}

describe('every icon comes through lib/icons', () => {
  const files = walk(SRC);

  it('finds files to check', () => {
    // A walker that returns nothing would pass every assertion below.
    expect(files.length).toBeGreaterThan(400);
  });

  it('and the door it guards is really a door', () => {
    const src = readFileSync(join(SRC, ALLOWED), 'utf8');
    expect(src, 'the door stopped re-exporting the set').toMatch(/export \* from 'lucide-react'/);
    expect(src, 'the icon TYPE is gone — 11 files import it')
      .toMatch(/export type \{[^}]*LucideIcon/);
  });

  it('the detector can actually fail', () => {
    expect(offendersIn('features/x/Thing.tsx', "import { Truck } from 'lucide-react';"))
      .toHaveLength(1);
    // Type imports are icons too — a `LucideIcon` taken from the library
    // is a second name for the thing the door exists to make swappable.
    expect(offendersIn('features/x/Thing.tsx', "import type { LucideIcon } from 'lucide-react';"))
      .toHaveLength(1);
    // A TEST that names the library is reported too. It is a file
    // somebody has to edit on a pack swap exactly like any other, and
    // the exemption above is one file wide on purpose.
    expect(offendersIn('mods/somewhere.test.tsx', "import { Truck } from 'lucide-react';"),
      'the exemption widened to every test file').toHaveLength(1);
    // And the door itself is not reported.
    expect(offendersIn(ALLOWED, "export * from 'lucide-react';")).toHaveLength(0);
  });

  it('nobody else names lucide-react', () => {
    const offenders = files.flatMap(
      (f) => offendersIn(f, readFileSync(join(SRC, f), 'utf8')));
    expect(offenders,
      "import it from `lib/icons` — a glyph named at the library cannot be swapped")
      .toEqual([]);
  });

  /** The door is only worth having if it carries what the app uses. A
   *  re-export that resolved to nothing would leave every icon
   *  undefined, which React renders as an empty element rather than an
   *  error — so this asks the module itself. */
  it('and the door actually carries the glyphs', async () => {
    const icons = await import('../lib/icons');
    for (const name of ['Truck', 'Bell', 'Wrench', 'Map', 'X'])
      expect(icons, `lib/icons does not carry ${name}`).toHaveProperty(name);
  });
});
