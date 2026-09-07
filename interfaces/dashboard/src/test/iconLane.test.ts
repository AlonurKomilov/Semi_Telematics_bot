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
import { ICON_NAMES } from '../lib/icons/names';
import { ICON_WEIGHTS } from '../lib/icons/weight';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');

/**
 * Who may name a set, and why.
 *
 * `lib/icons/index.tsx` is the door. The two pack modules beside it are
 * the only other places a library belongs — that is what a pack IS —
 * and they are named one by one rather than by folder, so a third file
 * appearing in there still has to justify itself.
 */
const ALLOWED = 'lib/icons/index.tsx';

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
/** Exports of the door that are not glyphs. */
const NOT_A_GLYPH = new Set([
  'LucideIcon', 'LucideProps', 'IconProps', 'IconComponent', 'IconPackProvider',
  'ICON_NAMES', 'ICON_WEIGHTS', 'ICON_PACKS', 'IconName', 'IconWeightName',
  'IconPack', 'RasterIconProps',
]);

const PACK_FILES = [
  'lib/icons/lucide.tsx', 'lib/icons/lucide.icons.ts',
  'lib/icons/phosphor.tsx', 'lib/icons/phosphor.icons.ts',
];

/** Comments out, first. A docstring EXPLAINING the rule reads exactly
 *  like a violation of it — `names.ts` describes the `export *` the door
 *  used to be, and was reported for saying so. This file's own trap,
 *  and the third time this codebase has met it. */
const code = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

function offendersIn(file: string, src: string): string[] {
  if (file === ALLOWED || file === SELF || PACK_FILES.includes(file)) return [];
  return [...code(src).matchAll(/^.*from '(lucide-react|@phosphor-icons\/react)'.*$/gm)]
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
    expect(src, 'the door stopped resolving a pack at render')
      .toMatch(/useContext\(PackContext\)/);
    expect(src, 'the icon TYPE is gone — 54 files name it')
      .toMatch(/export type LucideIcon/);
    // The fallback's ABSENCE, pinned by reading the resolver's body
    // rather than one spelling of the mistake: any mention of the base
    // pack inside `glyph` is a route by which a second vocabulary
    // reaches the screen.
    const body = src.slice(src.indexOf('function glyph('));
    expect(body, 'the resolver reached for the base pack — one set on screen at a time')
      .not.toMatch(/\bBASE\b|\blucide\b/);
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
    // Phosphor counts as much as lucide — the rule is about naming ANY
    // set outside a pack, not about one library.
    expect(offendersIn('features/x/Thing.tsx',
      "import { Truck } from '@phosphor-icons/react';")).toHaveLength(1);
    // A comment describing an import is not an import.
    expect(offendersIn('features/x/Thing.tsx',
      "/** was `import { Truck } from 'lucide-react'` */"),
      'a docstring explaining the rule was read as breaking it').toHaveLength(0);
    expect(offendersIn('features/x/Thing.tsx',
      "// import { Truck } from 'lucide-react';")).toHaveLength(0);
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

describe('one set on screen at a time', () => {
  /**
   * The assumption the whole design rests on.
   *
   * `lib/icons` has NO cross-pack fallback: a glyph the active pack does
   * not carry renders nothing rather than borrowing from another set,
   * because borrowing is the mixed screen the rule forbids. That is only
   * a safe choice while every pack carries every name — otherwise the
   * honest behaviour is a hole in the UI, silently, on whichever screen
   * used the missing glyph.
   *
   * So this is not a completeness nicety. It is what makes the missing
   * case unreachable.
   */
  /**
   * Read from the pack SOURCES, not by importing them.
   *
   * The import version made the suite flaky: pulling Phosphor's 3045
   * exports through the transform takes 15 to 23 seconds, over the 20s
   * timeout under load, and it had been that way since the pack landed.
   * Nothing is lost. Each pack file is a static re-export list, so the
   * names it carries are exactly what it declares — and whether those
   * names EXIST in the library is `tsc`'s answer, not a test's:
   * `export { NotAThing } from '@phosphor-icons/react'` does not
   * compile. Source here, compiler there, both fast.
   */
  const carriedBy = (pack: string): Set<string> => {
    const src = readFileSync(join(SRC, `lib/icons/${pack}.icons.ts`), 'utf8');
    const block = /export\s*\{([\s\S]*?)\}\s*from/.exec(src)?.[1] ?? '';
    return new Set(block.split(',')
      .map((e) => e.trim().split(/\s+as\s+/).pop()!.trim())
      .filter(Boolean));
  };

  it('every pack carries every name the app draws', () => {
    expect(ICON_NAMES.length, 'no names — this test would pass on nothing')
      .toBeGreaterThan(200);
    for (const pack of ['lucide', 'phosphor']) {
      const carried = carriedBy(pack);
      expect(carried.size, `${pack} declares nothing — this checked no names`)
        .toBe(ICON_NAMES.length);
      const missing = ICON_NAMES.filter((n) => !carried.has(n));
      expect(missing, `${pack} is missing ${missing.length} glyph(s)`).toEqual([]);
    }
  });

  /**
   * And each carries a way to take the weight, since weight is not one
   * mechanism across packs — lucide strokes, Phosphor names its six.
   *
   * TOTAL over the axis, which is the half that matters. A missing
   * entry resolves to `undefined`, the library falls back to its own
   * default, and the weight silently stops working — the mod applies,
   * the icons do not change, and it reads as the feature being broken
   * rather than as a typo. The maps live in their own leaves so this
   * costs an import of two small objects rather than of two libraries.
   */
  it('and every pack takes the weight its own way, for every weight', async () => {
    const maps = await Promise.all([
      import('../lib/icons/lucide.weights'), import('../lib/icons/phosphor.weights'),
    ]);
    expect(ICON_WEIGHTS.length, 'no weights — this test would pass on nothing').toBe(3);
    for (const [id, mod] of [['lucide', maps[0]], ['phosphor', maps[1]]] as const)
      for (const w of ICON_WEIGHTS)
        expect(mod.WEIGHT_MAP[w], `${id} names nothing for "${w}"`).toBeDefined();
    // Lucide's own default, kept where the numbers are: `regular` must
    // be 2 or the base pack is drawn unlike everything shipped before.
    expect(maps[0].WEIGHT_MAP.regular, "regular must be lucide's own default").toBe(2);
  });

  /** The providers are still the thing that installs a weight, and a
   *  pack without one cannot be worn. Asserted on the source, for the
   *  same reason as above. */
  it('and every pack ships a provider', () => {
    for (const pack of ['lucide', 'phosphor'])
      expect(readFileSync(join(SRC, `lib/icons/${pack}.tsx`), 'utf8'),
        `${pack} has no weight provider`).toMatch(/export function Provider/);
  });

  /** The inventory is the contract both packs answer to, so it must be
   *  exactly what `src` uses — no more, no less. A name used and unlisted
   *  fails to compile; a name listed and unused is dead weight carried
   *  into every pack, including the one that is fetched. */
  it('and the inventory is exactly what the app imports', () => {
    const used = new Set<string>();
    for (const f of walk(SRC)) {
      if (f.startsWith('lib/icons')) continue;
      const src = readFileSync(join(SRC, f), 'utf8');
      for (const m of code(src).matchAll(
        /import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+'[^']*lib\/icons'/g)) {
        for (const part of m[1].split(',')) {
          const n = part.trim().replace(/^type\s+/, '').split(/\s+as\s+/)[0].trim();
          if (n && /^[A-Z]/.test(n) && !NOT_A_GLYPH.has(n)) used.add(n);
        }
      }
    }
    expect(used.size, 'no imports parsed — this test measures nothing')
      .toBeGreaterThan(200);
    const listed = new Set<string>(ICON_NAMES);
    expect([...used].filter((n) => !listed.has(n)).sort(),
      'drawn but not in names.ts').toEqual([]);
    expect([...listed].filter((n) => !used.has(n)).sort(),
      'in names.ts but drawn nowhere — dead weight in every pack').toEqual([]);
  });
});
