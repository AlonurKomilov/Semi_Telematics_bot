/**
 * The corner ramp, pinned by its numbers.
 *
 * `rounded-md` used to compile to `calc(var(--radius) - 2px)` inside
 * the Tailwind config; it reads `var(--radius-md)` now, and the
 * arithmetic sits in `index.css` under a name an override can take.
 * That is a refactor of 700-odd call sites through one file, which is
 * exactly the kind nobody can review by reading — so this evaluates
 * both halves for every radius the app ships and holds the results to
 * the numbers they had before.
 *
 * A change to the ramp is then a deliberate act: the numbers below move
 * with it, in the same commit, on purpose.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = join(__dirname, '..', '..', '..');
const css = readFileSync(join(ROOT, 'src', 'index.css'), 'utf8');
const tw = readFileSync(join(ROOT, 'tailwind.config.js'), 'utf8');

/** The first definition of a custom property — `:root`'s. */
const tokenOf = (name: string): string | undefined =>
  new RegExp(`--${name}:\\s*([^;]+);`).exec(css)?.[1].trim();

/** What Tailwind emits for each `rounded-*` step. */
const STEPS = (() => {
  const block = /borderRadius:\s*\{([\s\S]*?)\n\s{6}\},/.exec(tw)?.[1] ?? '';
  return Object.fromEntries([...block.matchAll(/'?([\w-]+)'?:\s*'([^']+)'/g)]
    .map((m) => [m[1], m[2]]));
})();

/**
 * Every ramp the app ships: the base, and one per corner item.
 *
 * An item overrides EITHER the base (`--radius`, which slides the whole
 * ramp — Sharp and Pill) OR individual steps (which changes the shape
 * of it — Soft panels). Both are read the same way: whatever custom
 * properties the file sets become an overlay over `:root`.
 *
 * Read from the FOLDER, so a corner that ships without a pinned line
 * below fails here rather than going unmeasured.
 */
const CORNER_DIR = join(ROOT, 'src', 'mods', 'store', 'items', 'corners');
const RAMPS: { name: string; overlay: Record<string, string> }[] = [
  { name: 'rounded', overlay: {} },
  ...readdirSync(CORNER_DIR).filter((f) => f.endsWith('.css')).map((f) => {
    const body = readFileSync(join(CORNER_DIR, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const name = /\[data-radius="([\w-]+)"\]/.exec(body)?.[1];
    if (!name) throw new Error(`${f} stamps no [data-radius]`);
    const overlay = Object.fromEntries(
      [...body.matchAll(/--([\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
    if (!Object.keys(overlay).length) throw new Error(`${f} sets nothing`);
    return { name, overlay };
  }),
];

const toPx = (v: string) => v.replace(/([\d.]+)rem/g, (_, n) => `${Number(n) * 16}px`);

/** Substitute tokens until nothing is left to substitute — the item's
 *  own values first, then `:root`'s. */
const resolve = (expr: string, overlay: Record<string, string>): string => {
  let out = expr;
  for (let i = 0; i < 12; i += 1) {
    const next = out.replace(/var\(--([\w-]+)\)/g,
      (whole, n) => overlay[n] ?? tokenOf(n) ?? whole);
    if (next === out) return out;
    out = next;
  }
  throw new Error(`--radius tokens do not bottom out: ${expr}`);
};

const px = (expr: string): number => {
  const js = toPx(expr).replace(/max\(/g, 'Math.max(').replace(/calc\(/g, '(').replace(/px/g, '');
  if (!/^[-+*/(),.\d\sMathmx]+$/.test(js)) throw new Error(`unexpected expression: ${js}`);
  return Function(`"use strict";return (${js})`)() as number;
};

/** What every step measures, in px, at every ramp the app ships. The
 *  first three are what they were before the arithmetic moved. */
const PINNED: Record<string, Record<string, number>> = {
  //                sm  DEFAULT  md  lg  xl  2xl  3xl
  rounded:       { sm: 6, DEFAULT: 7, md: 8, lg: 10, xl: 14, '2xl': 18, '3xl': 26 },
  sharp:         { sm: 0, DEFAULT: 0, md: 0, lg: 0, xl: 4, '2xl': 8, '3xl': 16 },
  pill:          { sm: 12, DEFAULT: 13, md: 14, lg: 16, xl: 20, '2xl': 24, '3xl': 32 },
  // The shape changes rather than the scale: the four small steps are
  // Rounded's own, and only the three big ones move.
  'soft-panels': { sm: 6, DEFAULT: 7, md: 8, lg: 10, xl: 20, '2xl': 26, '3xl': 34 },
};

describe('the corner ramp is what it was', () => {
  it('found both halves to compare', () => {
    expect(Object.keys(STEPS).sort()).toEqual(['2xl', '3xl', 'DEFAULT', 'lg', 'md', 'sm', 'xl']);
    expect(RAMPS.map((r) => r.name).sort())
      .toEqual(['pill', 'rounded', 'sharp', 'soft-panels']);
  });

  it('every step, at every ramp the app ships, measures what it should', () => {
    for (const { name, overlay } of RAMPS) {
      const pinned = PINNED[name];
      expect(pinned, `"${name}" is shipped and pinned nowhere`).toBeTruthy();
      for (const [step, expr] of Object.entries(STEPS)) {
        expect(px(resolve(expr, overlay)), `rounded-${step} at "${name}"`).toBe(pinned[step]);
      }
    }
  });

  it('an item that moves one end leaves the other alone', () => {
    // The reason the steps became tokens. If every corner item can only
    // slide the whole ramp, nothing was gained and Pill already existed.
    const soft = RAMPS.find((r) => r.name === 'soft-panels')!;
    const base = PINNED.rounded;
    for (const step of ['sm', 'DEFAULT', 'md', 'lg'])
      expect(px(resolve(STEPS[step], soft.overlay)), `${step} moved`).toBe(base[step]);
    const moved = ['xl', '2xl', '3xl']
      .filter((s) => px(resolve(STEPS[s], soft.overlay)) !== base[s]);
    expect(moved, 'Soft panels moves no step at all').toEqual(['xl', '2xl', '3xl']);
  });

  it('and the config reaches a token, never arithmetic of its own', () => {
    // The whole point: a step has to be overridable on its own. A
    // `calc()` back in the config would take that away and nothing else
    // would notice — every number above would still be right.
    for (const [step, expr] of Object.entries(STEPS)) {
      expect(expr, `rounded-${step} computes in the config again`).toMatch(/^var\(--radius-[\w-]+\)$/);
    }
  });

  it('a negative radius cannot reach the page', () => {
    // Sharp is 0px and three steps subtract from it.
    const sharp = RAMPS.find((r) => r.name === 'sharp')!;
    for (const step of ['sm', 'DEFAULT', 'md']) {
      expect(px(resolve(STEPS[step], sharp.overlay)), `rounded-${step} at Sharp`).toBe(0);
    }
  });
});
