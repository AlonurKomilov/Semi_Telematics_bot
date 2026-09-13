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
import { readFileSync } from 'node:fs';
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

/** Every radius the app ships: the base, and each override. */
const SHIPPED: Record<string, string> = {
  rounded: tokenOf('radius') ?? '',
  ...Object.fromEntries([...css.matchAll(/\[data-radius="(\w+)"\]\s*\{\s*--radius:\s*([^;]+);/g)]
    .map((m) => [m[1], m[2].trim()])),
};

const toPx = (v: string) => v.replace(/([\d.]+)rem/g, (_, n) => `${Number(n) * 16}px`);

/** Substitute tokens until nothing is left to substitute. */
const resolve = (expr: string, radius: string): string => {
  let out = expr;
  for (let i = 0; i < 12; i += 1) {
    const next = out
      .replace(/var\(--radius\)/g, radius)
      .replace(/var\(--([a-z0-9-]+)\)/g, (whole, n) => tokenOf(n) ?? whole);
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

/** What every step measured before the arithmetic moved. 1rem = 16px. */
const PINNED: Record<string, Record<string, number>> = {
  //          sm  DEFAULT  md  lg  xl  2xl  3xl
  rounded: { sm: 6, DEFAULT: 7, md: 8, lg: 10, xl: 14, '2xl': 18, '3xl': 26 },
  sharp:   { sm: 0, DEFAULT: 0, md: 0, lg: 0, xl: 4, '2xl': 8, '3xl': 16 },
  pill:    { sm: 12, DEFAULT: 13, md: 14, lg: 16, xl: 20, '2xl': 24, '3xl': 32 },
};

describe('the corner ramp is what it was', () => {
  it('found both halves to compare', () => {
    expect(Object.keys(STEPS).sort()).toEqual(['2xl', '3xl', 'DEFAULT', 'lg', 'md', 'sm', 'xl']);
    expect(Object.keys(SHIPPED).sort()).toEqual(['pill', 'rounded', 'sharp']);
  });

  it('every step, at every radius the app ships, measures what it always did', () => {
    for (const [name, radius] of Object.entries(SHIPPED)) {
      const pinned = PINNED[name];
      expect(pinned, `"${name}" is shipped and pinned nowhere`).toBeTruthy();
      for (const [step, expr] of Object.entries(STEPS)) {
        expect(px(resolve(expr, toPx(radius))),
          `rounded-${step} at radius "${name}"`).toBe(pinned[step]);
      }
    }
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
    for (const step of ['sm', 'DEFAULT', 'md']) {
      expect(px(resolve(STEPS[step], '0px')), `rounded-${step} at Sharp`).toBe(0);
    }
  });
});
