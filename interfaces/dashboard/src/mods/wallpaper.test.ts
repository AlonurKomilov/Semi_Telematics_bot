/**
 * A pattern is readable, and it is the STYLESHEET that is measured.
 *
 * The canvas gate refuses a background whose semantic tones would fall
 * under AA-large, and it can only do that because a canvas is one
 * colour. A wallpaper is a range — and the reason this one is made of
 * `color-mix` over `--sidebar` rather than an image is that a range
 * built from tokens has knowable extremes.
 *
 * So the mix percentages are READ OUT OF `index.css`, not restated
 * here. A pattern whose stops drift past the ceiling fails, and it
 * fails on the file that paints them rather than on a copy of the
 * numbers that agrees with itself.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  WALLPAPERS, WALLPAPER_IDS, WALLPAPER_AA, WALLPAPER_BASE, WALLPAPER_INK,
} from './wallpaper';
import { THEME_PACKS } from './catalogue';
import { oklchToSrgb, contrastRatio, type RGB } from './theme/contrast';

const CSS = readFileSync(join(__dirname, '..', 'index.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

function body(selector: string): string {
  let out = '';
  for (const m of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g))
    if (m[1].trim().replace(/\s+/g, ' ') === selector) out += m[2];
  return out;
}
/**
 * A token's colour AND its alpha.
 *
 * The alpha half is not decoration: the dark `--sidebar-border` ships as
 * `oklch(1 0 0 / 10%)`, so a grid line drawn at 55% of it is 5.5% white
 * over the chrome and not 55% of anything. A parser that ignored the
 * slash read that token as null — which is how this was found — and one
 * that dropped it silently would have measured a line four times
 * stronger than the one on screen.
 */
function token(src: string, name: string): { rgb: RGB; alpha: number } | null {
  const m = new RegExp(
    `${name}:\\s*oklch\\(([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)\\s*(?:\\/\\s*([\\d.]+)(%?))?\\s*\\)`,
  ).exec(src);
  if (!m) return null;
  const alpha = m[4] === undefined ? 1 : (+m[4] / (m[5] === '%' ? 100 : 1));
  return { rgb: oklchToSrgb(+m[1], +m[2], +m[3]).rgb, alpha };
}
const mix = (a: RGB, b: RGB, pct: number): RGB =>
  a.map((v, i) => v * (1 - pct / 100) + b[i] * (pct / 100)) as RGB;

/**
 * Every stop a pattern declares: which token, and how much of it.
 *
 * Matched on `var(--x) N%` rather than on `color-mix(` — a lazy scan up
 * to the first `)` stops inside `var(--primary)` and finds nothing,
 * which is how this returned an empty list and reported the patterns as
 * painting images.
 */
function stopsOf(id: string): { token: string; pct: number }[] {
  const block = body(`:root[data-wallpaper="${id}"] .chrome-ground`);
  return [...block.matchAll(/var\((--[a-z-]+)\)\s+(\d+(?:\.\d+)?)%/g)]
    .map((m) => ({ token: m[1], pct: +m[2] }));
}

const MODE_CELL = { light: ':root', dark: '.dark' } as const;
const packCell = (id: string, mode: 'light' | 'dark') =>
  id === THEME_PACKS[0].id ? MODE_CELL[mode]
    : mode === 'light'
      ? `:root:not(.dark)[data-accent="${id}"]:not([data-mod-accent])`
      : `.dark[data-accent="${id}"]:not([data-mod-accent])`;

describe('the stylesheet answers for every pattern the list offers', () => {
  it('finds the tokens it measures against', () => {
    // Everything below is arithmetic on these; if they are null the
    // suite would be comparing undefined and passing.
    for (const mode of ['light', 'dark'] as const) {
      expect(token(body(MODE_CELL[mode]), WALLPAPER_BASE), `${mode} ${WALLPAPER_BASE}`).not.toBeNull();
      expect(token(body(MODE_CELL[mode]), WALLPAPER_INK), `${mode} ${WALLPAPER_INK}`).not.toBeNull();
      // The one token that ships translucent, asserted so a parser that
      // quietly dropped the alpha would be caught here rather than
      // measuring a line four times stronger than the painted one.
      expect(token(body(MODE_CELL.dark), '--sidebar-border')!.alpha,
        'the dark sidebar border stopped being translucent').toBeLessThan(1);
    }
    expect(WALLPAPERS.length).toBeGreaterThan(1);
  });

  it('gives every pattern but "none" a block, and "none" none', () => {
    for (const w of WALLPAPERS) {
      const has = body(`:root[data-wallpaper="${w.id}"] .chrome-ground`) !== '';
      if (w.id === 'none') {
        // Flat chrome is the absence of a rule, not a rule that undoes
        // one — an `background-image: none` block would still have to
        // win against whatever a later pattern declares.
        expect(has, '"none" has a block; flat chrome should be no block at all').toBe(false);
      } else {
        expect(has, `${w.id} is offered and paints nothing`).toBe(true);
      }
    }
  });

  it('and no block for a pattern the list does not offer', () => {
    const orphans = [...CSS.matchAll(/\[data-wallpaper="([^"]+)"\]/g)]
      .map((m) => m[1]).filter((id) => !WALLPAPER_IDS.includes(id));
    expect([...new Set(orphans)], 'CSS paints a pattern nobody can choose').toEqual([]);
  });

  it('every pattern is made of token mixes, never an image', () => {
    // The property that keeps the whole thing measurable. A `url()`
    // here would be a background nothing below can reason about — and
    // would need the reviewed path `inject.ts` says an image needs.
    let counted = 0;
    for (const w of WALLPAPERS.filter((x) => x.id !== 'none')) {
      const block = body(`:root[data-wallpaper="${w.id}"] .chrome-ground`);
      expect(block, `${w.id} reaches for a URL`).not.toMatch(/url\(/);
      const stops = stopsOf(w.id);
      expect(stops.length, `${w.id} declares no token mix — what is it painting?`)
        .toBeGreaterThan(0);
      counted += stops.length;
    }
    expect(counted, 'no stops parsed — every measurement below is vacuous')
      .toBeGreaterThan(2);
  });
});

describe('the ground is where it can be seen', () => {
  /**
   * The three surfaces of the chrome envelope, which AppShell's own
   * comment names: "Sidebar, header and gutters are all `bg-sidebar` —
   * one continuous chrome surface". Each paints the chrome colour flat,
   * so each has to step aside for a pattern or it covers it.
   *
   * A NAMED THREE rather than "everything that uses bg-sidebar": the
   * assistant panel and the mobile drawer wear the same colour as
   * panels OVER content, and making those transparent would make them
   * see-through. The envelope is a concept, not a colour.
   *
   * This is the shape of the bug it exists for. The first version put
   * the ground UNDER the header and outside the sidebar entirely, so it
   * painted only into the 8px gutter and reached the owner as "how do I
   * see the wallpaper?".
   */
  const CHROME = {
    'shells/AppShell.tsx': 'the envelope and the header',
    'components/Sidebar.tsx': 'the sidebar',
  };

  it('every chrome surface steps aside for it', () => {
    let checked = 0;
    for (const [file, what] of Object.entries(CHROME)) {
      const src = readFileSync(join(__dirname, '..', file), 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '');
      // Each className that paints the chrome colour, on its own. The
      // three delimiters are matched separately because only the
      // delimiter ends its own value: the sidebar's class is a template
      // literal with `'w-14'` inside it, and a pattern that stopped at
      // any quote read it as two values and found neither.
      const CLASSNAME = new RegExp(
        [
          'className="[^"]*"',
          'className=\\{`[^`]*`\\}',
          "className=\\{'[^']*'\\}",
        ].join('|'), 'g',
      );
      for (const m of src.match(CLASSNAME)?.filter((c) => /\bbg-sidebar\b/.test(c)) ?? []) {
        checked++;
        expect(m, `${what}: a chrome surface paints bg-sidebar and never steps aside`)
          .toMatch(/\bchrome-pane\b/);
      }
    }
    expect(checked, 'no chrome surfaces found — this test measures nothing')
      .toBeGreaterThanOrEqual(3);
  });

  it('and the ground carries the chrome colour itself', () => {
    // Without it the panes go transparent onto `bg-background` and the
    // whole chrome turns the content card's colour — the pattern would
    // arrive and the app would look broken around it.
    for (const w of WALLPAPERS.filter((x) => x.id !== 'none')) {
      const block = body(`:root[data-wallpaper="${w.id}"] .chrome-ground`);
      expect(block, `${w.id} paints a pattern on no ground`)
        .toMatch(/background-color:\s*var\(--sidebar\)/);
    }
  });
});

describe('the sidebar stays readable over any of them', () => {
  /**
   * Every stop, where it actually lands.
   *
   * The accent is iterated over every shipped pack, because it is the
   * one token under a pattern that a person can change — a wallpaper
   * that reads on blue and fails on green would fail for whoever chose
   * green and nobody else.
   */
  it('every declared stop clears AA, under every accent, in both modes', () => {
    let worst = Infinity, where = '';
    let measured = 0;
    for (const mode of ['light', 'dark'] as const) {
      const base = token(body(MODE_CELL[mode]), WALLPAPER_BASE)!.rgb;
      const ink = token(body(MODE_CELL[mode]), WALLPAPER_INK)!.rgb;
      for (const w of WALLPAPERS.filter((x) => x.id !== 'none')) {
        for (const stop of stopsOf(w.id)) {
          // `--primary` moves with the pack; everything else is one
          // value per mode.
          const packs = stop.token === '--primary'
            ? THEME_PACKS.map((p) => [p.id, token(body(packCell(p.id, mode)), '--primary')
                ?? token(body(MODE_CELL[mode]), '--primary')!] as const)
            : [['—', token(body(MODE_CELL[mode]), stop.token)!] as const];
          for (const [pack, colour] of packs) {
            expect(colour, `${stop.token} is not declared in ${mode}`).not.toBeNull();
            // The stop's own percentage times the token's alpha — what
            // reaches the screen, not what the declaration says.
            const r = contrastRatio(ink, mix(base, colour!.rgb, stop.pct * colour!.alpha));
            measured++;
            if (r < worst) { worst = r; where = `${w.id} ${stop.token}@${stop.pct}% ${pack}/${mode}`; }
            expect(r, `sidebar ink over ${w.id} ${stop.token} at ${stop.pct}% (${pack}/${mode})`)
              .toBeGreaterThanOrEqual(WALLPAPER_AA);
          }
        }
      }
    }
    expect(measured, 'nothing was measured').toBeGreaterThan(8);
    // The headroom, recorded rather than described — so a palette change
    // that erodes it toward AA is a number moving in a diff rather than
    // a slow drift nobody sees until it fails.
    expect(worst, `worst is ${where} at ${worst.toFixed(2)}`).toBeGreaterThan(12);
  });

  /** The control. Ten percent barely moves ink this far from its
   *  ground, so a passing measurement has to be shown to be a
   *  measurement — at a mix nobody would ship, it must fail. */
  it('and the measurement can fail — at a mix nobody would ship, it does', () => {
    const base = token(body(MODE_CELL.dark), WALLPAPER_BASE)!.rgb;
    const ink = token(body(MODE_CELL.dark), WALLPAPER_INK)!.rgb;
    const primary = token(body(MODE_CELL.dark), '--primary')!.rgb;
    expect(contrastRatio(ink, mix(base, primary, 95))).toBeLessThan(WALLPAPER_AA);
  });
});
