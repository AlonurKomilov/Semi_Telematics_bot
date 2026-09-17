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
 * change did not touch. Lowering `--surface-wash-page` to anything at all
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
import { oklchToSrgb, srgbToOklch, parseHex, contrastRatio, AA_TEXT, type RGB } from './contrast';
import { PATTERN_STOPS } from './palette';
import { THEME_PACKS } from '../store/items/theme';

const GLASS = readFileSync(
  join(__dirname, '..', 'store', 'items', 'material', 'glass.css'), 'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '');

const CSS = readFileSync(join(__dirname, '..', '..', 'index.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

/**
 * A token's LIGHTNESS, out of the stylesheet.
 *
 * `:root` is several separate blocks and the cascade merges them, so
 * every matching block is concatenated before the token is read —
 * taking only the first finds the font stacks and no colour at all.
 * Chroma is dropped: the largest here is `--sidebar`'s 0.022, small
 * enough that the mix is dominated by L.
 */
function tokenL(selector: string, name: string): number {
  let body = '';
  for (const m of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g))
    if (m[1].trim().replace(/\s+/g, ' ') === selector) body += m[2];
  const v = new RegExp(`${name}:\\s*oklch\\(([\\d.]+)`).exec(body);
  if (!v) throw new Error(`index.css states no ${name} for ${selector}`);
  return Number(v[1]);
}

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

/**
 * The same declaration, as the CASCADE hands it to one mode.
 *
 * `.dark[data-material="glass"]` is the SAME ELEMENT as
 * `:root[data-material="glass"]` — `<html>` carries both — so a token
 * the dark block does not restate is not missing, it is inherited, and
 * a reader that only looks in the mode block reports "unstated" for a
 * value the browser resolves perfectly well. That matters now that the
 * pane's own properties are declared once: the alpha dark mode uses is
 * the base one, and modelling dark without it would model nothing.
 */
const declFor = (selector: string, prop: string): number => {
  try {
    return decl(selector, prop);
  } catch {
    return decl(LIGHT, prop);
  }
};

/**
 * The planes and inks, READ from index.css rather than copied beside
 * it — the same rule the pack's own numbers now follow, and for the
 * same reason: a guard holding its own copy of the value it guards is
 * not a guard.
 */
const LIGHT_SEL = ':root', DARK_SEL = '.dark';
const modeTokens = (sel: string) => ({
  background: tokenL(sel, '--background'),
  sidebar: tokenL(sel, '--sidebar'),
  card: tokenL(sel, '--card'),
  popover: tokenL(sel, '--popover'),
  fg: tokenL(sel, '--foreground'),
  mutedFg: tokenL(sel, '--muted-foreground'),
  /**
   * THE INK CHANGES UNDER A PATTERN, and measuring the plain one there
   * was this file's own bug for exactly one commit. `index.css` swaps
   * `--muted-foreground` for this on `.page-ground` whenever a page
   * wallpaper is on, and a custom property inherits — so a Card inside
   * that ground is already writing its secondary text in this ink, not
   * the other. Holding the patterned case to the plain ink reported a
   * product that does not exist, and reported it as nearly failing.
   */
  mutedFgOnPattern: tokenL(sel, '--muted-foreground-on-pattern'),
});

const T = {
  light: { ...modeTokens(LIGHT_SEL), alpha: declFor(LIGHT, '--surface-wash-page'), sheen: declFor(LIGHT, '--glass-sheen') },
  dark: { ...modeTokens(DARK_SEL), alpha: declFor(DARK, '--surface-wash-page'), sheen: declFor(DARK, '--glass-sheen') },
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
                   groundL: number): number {
  const t = T[mode];
  const fill = t[surface] * t.alpha + groundL * (1 - t.alpha);
  const tinted = fill * 0.90 + groundL * 0.10;
  const s = 0.16 * t.sheen;
  return tinted * (1 - s) + 1 * s;
}

/**
 * The grounds a translucent surface can actually sit on.
 *
 * Only the IN-FLOW ones, and that used to be a fact about the pack
 * rather than a simplification: everything positioned out of flow —
 * every popover, dialog, menu, sheet, sticky header and the frame
 * itself — was flattened by the escape hatch further down `glass.css`,
 * so there was never page CONTENT behind a translucent surface, only
 * the page's own ground. That is what makes clarity affordable here and
 * would not be in a product that frosted its dialogs.
 *
 * WHILE THE OWNER IS LOOKING AT CLEAR MENUS IT IS NOT TRUE, and this
 * says so rather than keeping a sentence that has stopped holding. The
 * floating set is not modelled here either way — its backdrop is
 * arbitrary content, which no palette bounds and no sweep can stand in
 * for — so nothing measured below moves. What moved is the REASON the
 * omission is safe, and during the evaluation it is not. The exemption
 * that permits it, and the cost it carries, are named in
 * `material.test.ts`.
 *
 * Third entry: that ground WEARING A WALLPAPER. A pattern is not free
 * to do anything — `PATTERN_STOPS` is the strongest stop any pack may
 * lay down and `wallpaper.test.ts` holds every pack under it — so the
 * worst ground a pattern can make is computable, and a surface you can
 * see through is exactly the surface that has to survive it.
 */
const SURFACES = ['card', 'popover'] as const;
const TEXTS = ['fg', 'mutedFg'] as const;

/** Every accent this app ships, as an L. A custom accent is bounded by
 *  its own guard against the canvas; these are the ones a pack sets. */
const ACCENT_L = THEME_PACKS.flatMap((p) =>
  (['light', 'dark'] as const).map((m) => srgbToOklch(parseHex(p.seed[m])!).L));

/** The worst L a pattern can turn a ground into, for text of this L.
 *  Worst = furthest from the text, so the three stops are tried and the
 *  one that hurts most is kept. */
function patterned(ground: number, textL: number): number {
  const mix = (target: number, pct: number) => ground * (1 - pct / 100) + target * (pct / 100);
  const stops = [
    mix(1, PATTERN_STOPS.white),
    mix(0, PATTERN_STOPS.black),
    ...ACCENT_L.map((L) => mix(L, PATTERN_STOPS.brand)),
  ];
  return stops.reduce((w, g) => (Math.abs(g - textL) < Math.abs(w - textL) ? g : w));
}

describe('glass still admits the text that sits on it', () => {
  for (const mode of ['light', 'dark'] as const) {
    for (const surface of SURFACES) {
      for (const ground of ['background', 'sidebar', 'background+wallpaper'] as const) {
        for (const text of TEXTS) {
          it(`${mode}: ${text} on ${surface} over ${ground}`, () => {
            const t = T[mode];
            const base = t[surface];
            const flat = ground === 'sidebar' ? 'sidebar' : 'background';
            const onPattern = ground === 'background+wallpaper';
            // Under a pattern the page swaps the secondary ink; the
            // primary one is unchanged, so only `mutedFg` moves.
            const inkL = onPattern && text === 'mutedFg' ? t.mutedFgOnPattern : t[text];
            const groundL = onPattern ? patterned(t.background, inkL) : t[flat];
            const glassL = composite(mode, surface, groundL);

            const solid = contrastRatio(grey(inkL), grey(base));
            const glass = contrastRatio(grey(inkL), grey(glassL));

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
