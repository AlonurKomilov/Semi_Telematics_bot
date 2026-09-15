/**
 * Glass is a post-process, and it composites UNDER the proof.
 *
 * `contrast.ts` clamps every derived colour to AA and the cube sweeps
 * prove it — about TOKEN VALUES. Under `[data-material="glass"]` a
 * `.surface` stops painting its base: it paints
 * `color-mix(in oklab, <base> <alpha>%, transparent)` over whatever is
 * behind it, and then paints two more layers on top of that. The text
 * sits on the whole stack, and `.surface` is on the Card primitive — so
 * this is every Card in the app, not a corner case.
 *
 * THE NUMBERS ARE READ FROM THE PACK, and that is the point of this
 * file rather than a detail of it. They used to be typed here as a
 * COPY of `glass.css`, which meant the one guard standing between a
 * clarity change and unreadable body text was measuring a number the
 * change did not touch. Lowering `--surface-alpha` to anything at all
 * left this suite green. A guard that holds its own copy of the value
 * it guards is not a guard.
 *
 * Every token here is achromatic except `--sidebar`, whose chroma is
 * 0.022 — small enough that the mix is dominated by L, and modelled as
 * such. The mix is computed in OKLAB because that is the space the CSS
 * names; mixing in sRGB would flatter the result.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { oklchToSrgb, contrastRatio, AA_TEXT, type RGB } from './contrast';

const GLASS = readFileSync(
  join(__dirname, '..', 'store', 'items', 'material', 'glass.css'), 'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '');

/**
 * One declaration, out of the pack.
 *
 * `[^{}]` on BOTH sides, never `[^}]`: the file is wrapped in
 * `@media screen`, and a body pattern that admits `{` swallows every
 * rule inside the at-rule and reports the first one for all of them.
 * That exact mistake shipped once in the wallpaper scanner and only a
 * mutation caught it.
 */
function decl(selector: string, prop: string): number {
  for (const m of GLASS.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (m[1].trim().replace(/\s+/g, ' ') !== selector) continue;
    const v = new RegExp(`${prop}:\\s*([\\d.]+)`).exec(m[2]);
    if (v) return Number(v[1]);
  }
  throw new Error(`glass.css states no ${prop} for ${selector}`);
}

const LIGHT = ':root[data-material="glass"]';
const DARK = '.dark[data-material="glass"]';

/** L, read from index.css. Chroma dropped: the largest is 0.022. */
const T = {
  light: {
    background: 1, sidebar: 0.965,
    card: 1, popover: 1,
    fg: 0.145, mutedFg: 0.545,
    alpha: decl(LIGHT, '--surface-alpha'),
    sheen: decl(LIGHT, '--glass-sheen'),
  },
  dark: {
    background: 0.10, sidebar: 0.215,
    card: 0.275, popover: 0.32,
    fg: 0.985, mutedFg: 0.70,
    alpha: decl(DARK, '--surface-alpha'),
    sheen: decl(DARK, '--glass-sheen'),
  },
} as const;

const grey = (L: number): RGB => oklchToSrgb(L, 0, 0).rgb;

/**
 * What the text actually sits on, top of the pane.
 *
 * Three layers, in the order the browser paints them: the translucent
 * FILL over the ground, then the pack's ground TINT, then the SHEEN.
 * The sheen is measured at the top of the surface because that is
 * where it is strongest AND where a card's title sits — and it is
 * white, so in dark mode it pushes the surface toward the text rather
 * than away from it. Modelling only the fill, as this file used to,
 * reports a contrast the reader never gets.
 */
function composite(mode: 'light' | 'dark', surface: 'card' | 'popover',
                   ground: 'background' | 'sidebar'): number {
  const t = T[mode];
  const fill = t[surface] * t.alpha + t[ground] * (1 - t.alpha);
  const tinted = fill * 0.90 + t[ground] * 0.10;
  const s = 0.16 * t.sheen;
  return tinted * (1 - s) + 1 * s;
}

/** Every surface a `.surface` can be, on every ground it can sit on. */
const SURFACES = ['card', 'popover'] as const;
const GROUNDS = ['background', 'sidebar'] as const;
const TEXTS = ['fg', 'mutedFg'] as const;

describe('glass still admits the text that sits on it', () => {
  for (const mode of ['light', 'dark'] as const) {
    for (const surface of SURFACES) {
      for (const ground of GROUNDS) {
        for (const text of TEXTS) {
          it(`${mode}: ${text} on ${surface} over ${ground}`, () => {
            const t = T[mode];
            const base = t[surface];
            const glassL = composite(mode, surface, ground);

            const solid = contrastRatio(grey(t[text]), grey(base));
            const glass = contrastRatio(grey(t[text]), grey(glassL));

            expect(solid, 'the solid path already fails — glass is not the bug')
              .toBeGreaterThanOrEqual(AA_TEXT);
            expect(
              glass,
              `proved at ${solid.toFixed(2)}:1 on the solid surface, `
              + `composites at ${glass.toFixed(2)}:1 under glass`,
            ).toBeGreaterThanOrEqual(AA_TEXT);
          });
        }
      }
    }
  }
});
