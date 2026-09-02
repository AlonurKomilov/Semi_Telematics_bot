/**
 * Glass is a post-process, and it composites UNDER the proof.
 *
 * `contrast.ts` clamps every derived colour to AA and the cube sweeps
 * prove it — about TOKEN VALUES. Under `[data-material="glass"]` a
 * `.surface` stops painting its base: it paints
 * `color-mix(in oklab, <base> <alpha>%, transparent)` over whatever is
 * behind it, and the text sits on that composite. Nothing measured the
 * composite, and `.surface` is on the Card primitive — so this is every
 * Card in the app, not a corner case.
 *
 * Every token here is achromatic except `--sidebar`, whose chroma is
 * 0.022 — small enough that the mix is dominated by L, and modelled as
 * such. The mix is computed in OKLAB because that is the space the CSS
 * names; mixing in sRGB would flatter the result.
 *
 * This was written expecting a failure. It found none — see the note in
 * the commit. It stays because the composite is unmeasured otherwise,
 * and because the next surface, ground or alpha will not be so lucky.
 */
import { describe, it, expect } from 'vitest';
import { oklchToSrgb, contrastRatio, AA_TEXT, type RGB } from './contrast';

/** L, read from index.css. Chroma dropped: the largest is 0.022. */
const T = {
  light: {
    background: 1, sidebar: 0.965,
    card: 1, popover: 1,
    fg: 0.145, mutedFg: 0.545,
    alpha: 0.72,
  },
  dark: {
    background: 0.10, sidebar: 0.215,
    card: 0.275, popover: 0.32,
    fg: 0.985, mutedFg: 0.70,
    alpha: 0.62,
  },
} as const;

const grey = (L: number): RGB => oklchToSrgb(L, 0, 0).rgb;

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
            const composite = base * t.alpha + t[ground] * (1 - t.alpha);

            const solid = contrastRatio(grey(t[text]), grey(base));
            const glass = contrastRatio(grey(t[text]), grey(composite));

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
