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
import { WALLPAPER_AA, WALLPAPER_BASE, WALLPAPER_INK, } from './wallpaper';
import { WALLPAPERS, WALLPAPER_IDS } from './packs/wallpaper';
import { assembledCss, engineCss } from '../test/stylesheet';
import { THEME_PACKS } from './packs/theme';
import { oklchToSrgb, contrastRatio, distance, type RGB } from './theme/contrast';
import {
  LIVE_ANIMATES, WALLPAPER_LIVE_ATTR, WALLPAPER_VISIBLE,
  WALLPAPER_PAGE_BASE, WALLPAPER_PAGE_INKS, WALLPAPER_PAGE_ATTR,
} from './wallpaper';

const CSS = assembledCss()
  .replace(/\/\*[\s\S]*?\*\//g, '');

/** The declarations of every rule whose selector LIST names `selector`
 *  — a pack rule names both grounds in one list, each under its own
 *  attribute, and either name reaches the same declarations. */
function body(selector: string): string {
  let out = '';
  for (const m of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g))
    if (m[1].split(',').some((s) => s.trim().replace(/\s+/g, ' ') === selector)) out += m[2];
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
const hex = (h: string): RGB =>
  [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255) as unknown as RGB;

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
/**
 * Everything a pattern paints — the ground's own rule AND any layer it
 * hangs off it. A live pattern keeps its clouds on a `::before` that
 * only transforms; the stops live there, and a gate that read the
 * ground alone would measure a live pattern's tooth and call it safe.
 */
/** The frame's ground under a pattern; the page's is its twin under
 *  `data-wallpaper-page`, and `pageGroundOf` names it. */
const groundOf = (id: string) => `:root[data-wallpaper="${id}"] .chrome-ground`;
const pageGroundOf = (id: string) => `:root[${WALLPAPER_PAGE_ATTR}="${id}"] .page-ground`;

function paintOf(id: string): string {
  const ground = groundOf(id);
  return body(ground) + body(`${ground}::before`) + body(`${ground}::after`);
}

function stopsOf(id: string): { token: string; pct: number }[] {
  const block = paintOf(id);
  const mixes = [...block.matchAll(/var\((--[a-z-]+)\)\s+(\d+(?:\.\d+)?)%/g)]
    .map((m) => ({ token: m[1], pct: +m[2] }));
  // A generated texture is greyscale by construction (`saturate 0`), so
  // it spans BLACK to WHITE at the opacity its URI states. Both ends are
  // stops, and measuring both is what makes a turbulence as answerable
  // as a `color-mix` — there is no token to name, so the extremes stand
  // in for one.
  const svg = [...block.matchAll(/opacity='(\d*\.?\d+)'/g)]
    .flatMap((m) => [
      { token: '#000000', pct: +m[1] * 100 },
      { token: '#ffffff', pct: +m[1] * 100 },
    ]);
  return [...mixes, ...svg];
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
      const has = body(groundOf(w.id)) !== '';
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

  it('nothing is fetched — every pattern is generated here', () => {
    // The property that keeps the whole thing measurable. A NETWORK url
    // would be a picture nobody here has seen, and would need the
    // reviewed path `inject.ts` says an image needs. An inline
    // `feTurbulence` is not that: it is generated in the browser from a
    // few hundred bytes, and being greyscale it has knowable extremes.
    let counted = 0;
    for (const w of WALLPAPERS.filter((x) => x.id !== 'none')) {
      const block = body(groundOf(w.id));
      for (const m of block.matchAll(/url\((['"]?)([^'")]*)/g)) {
        // `url(%23g)` inside the SVG is a filter REFERENCE — a pointer
        // to an element in the same document, not a fetch. Only the
        // outer, quoted one names something to load.
        if (m[2].startsWith('%23')) continue;
        expect(m[2], `${w.id} fetches its background`).toMatch(/^data:image\/svg\+xml,/);
      }
      const stops = stopsOf(w.id);
      expect(stops.length, `${w.id} declares nothing measurable — what is it painting?`)
        .toBeGreaterThan(0);
      counted += stops.length;
    }
    expect(counted, 'no stops parsed — every measurement below is vacuous')
      .toBeGreaterThan(2);
  });

  /** A texture that does not tile seamlessly shows a grid of seams
   *  across the chrome — the one artefact that reads as a rendering bug
   *  rather than as a pattern. `feTurbulence` needs telling. */
  it('and every generated texture stitches its tiles', () => {
    let seen = 0;
    for (const w of WALLPAPERS.filter((x) => x.id !== 'none')) {
      const block = body(groundOf(w.id));
      for (const m of block.matchAll(/feTurbulence[^%]*?%3E/g)) {
        seen++;
        expect(m[0], `${w.id} tiles its noise without stitching`)
          .toMatch(/stitchTiles='stitch'/);
      }
    }
    expect(seen, 'no turbulence found — this checked nothing').toBeGreaterThan(2);
  });

  /**
   * And drains it of colour, which is the whole reason a turbulence can
   * be measured at all.
   *
   * `feTurbulence` renders COLOURED noise. Greyscale, its extremes are
   * black and white at the stated opacity and the gate below holds the
   * ink over both. Without the matrix the extremes are arbitrary RGB,
   * the two stops this file measures are the wrong two, and every
   * reading under them is about a texture that is not on screen.
   */
  it('and drains it of colour, so its extremes are knowable', () => {
    let seen = 0;
    for (const w of WALLPAPERS.filter((x) => x.id !== 'none')) {
      const block = body(groundOf(w.id));
      for (const m of block.matchAll(/%3Cfilter[\s\S]*?%3C\/filter%3E/g)) {
        seen++;
        expect(m[0], `${w.id} generates coloured noise — its extremes are unknown`)
          .toMatch(/feColorMatrix type='saturate' values='0'/);
      }
    }
    expect(seen, 'no filters found — this checked nothing').toBeGreaterThan(2);
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

  it('and the ground carries its own colour under the pattern', () => {
    // Without it the panes go transparent onto `bg-background` and the
    // whole chrome turns the content card's colour — the pattern would
    // arrive and the app would look broken around it. `var(--ground)`,
    // not `var(--sidebar)`: the engine resolves it to the sidebar on the
    // frame and to the page colour on the page, so one pack paints both.
    for (const w of WALLPAPERS.filter((x) => x.id !== 'none')) {
      const block = body(groundOf(w.id));
      expect(block, `${w.id} paints a pattern on no ground`)
        .toMatch(/background-color:\s*var\(--ground\)/);
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
            : stop.token.startsWith('#')
              // A greyscale texture's extreme, which is a colour rather
              // than a token — there is nothing in the stylesheet to
              // look up, and that is the point of standing in for one.
              ? [['—', { rgb: hex(stop.token), alpha: 1 }] as const]
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

/**
 * A live pattern is a still one that moves a layer — and only moves it.
 *
 * The contrast gate above measures stops. It cannot measure a colour
 * that appears mid-animation, so a live pattern's keyframes may touch
 * the compositor's properties and nothing else: a layer that only
 * transforms is drawn once and moved by the GPU, and its stops are the
 * same stops at every frame. Everything here is held against the
 * shipped packs by `kind`, both ways — a still pack with keyframes is a
 * live pack lying about its cost.
 *
 * And it plays ONLY under `data-wallpaper-live`. A pattern that can move
 * is one pack in two states, not two packs; the stylesheet must draw the
 * still state whenever the switch is off, and a keyframe played from an
 * ungated rule would be a pattern moving unasked.
 */
describe('a live pattern moves only what the gate has already measured', () => {
  const live = WALLPAPERS.filter((w) => w.kind === 'live');
  const still = WALLPAPERS.filter((w) => w.kind === 'still' && w.id !== 'none');
  const fileOf = (id: string) =>
    readFileSync(join(__dirname, 'packs', 'wallpaper', `${id}.css`), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  /** Property names set inside every `@keyframes` block of a file. */
  const animated = (css: string): string[] =>
    [...css.matchAll(/@keyframes\s+[\w-]+\s*\{([\s\S]*?)\}\s*\}/g)]
      .flatMap((m) => [...m[1].matchAll(/([a-z-]+)\s*:/g)].map((p) => p[1]));

  it('there is at least one of each, or the rules below hold nothing', () => {
    expect(live.length).toBeGreaterThan(0);
    expect(still.length).toBeGreaterThan(0);
  });

  it('still packs have no keyframes; live packs do', () => {
    for (const w of still)
      expect(fileOf(w.id), `${w.id} is "still" and animates`).not.toMatch(/@keyframes|animation/);
    for (const w of live) {
      expect(fileOf(w.id), `${w.id} is "live" and has no keyframes`).toMatch(/@keyframes/);
      expect(fileOf(w.id), `${w.id} declares keyframes and never plays them`).toMatch(/animation:/);
    }
  });

  it('and plays them only while the Live switch is on', () => {
    for (const w of live) {
      const rules = [...fileOf(w.id).matchAll(/([^{}]+)\{([^{}]*)\}/g)]
        .filter(([, , decls]) => /animation:/.test(decls) && !/animation:\s*none/.test(decls));
      expect(rules.length, `${w.id}: no rule plays the animation`).toBeGreaterThan(0);
      for (const [, selector] of rules)
        for (const name of selector.split(','))
          expect(name, `${w.id} moves without the switch: ${name.trim()}`)
            .toContain(`[${WALLPAPER_LIVE_ATTR}]`);
    }
  });

  it('keyframes animate only the compositor properties', () => {
    for (const w of live) {
      const props = animated(fileOf(w.id));
      expect(props.length, `${w.id}: no property parsed from its keyframes`).toBeGreaterThan(0);
      for (const p of props)
        expect(LIVE_ANIMATES as readonly string[], `${w.id} animates "${p}" — an extreme the gate never measured`)
          .toContain(p);
    }
  });

  it('and the parser can fail', () => {
    expect(animated('@keyframes x { from { opacity: 0 } to { opacity: 1; transform: none } }'))
      .toEqual(['opacity', 'opacity', 'transform']);
  });

  it('holds still under prefers-reduced-motion, and follows the Motion axis', () => {
    for (const w of live) {
      const css = fileOf(w.id);
      const reduced = /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{([\s\S]*?)\}\s*\}/.exec(css)?.[1] ?? '';
      expect(reduced, `${w.id} keeps moving for somebody who asked for less motion`).toMatch(/animation:\s*none/);
      expect(css, `${w.id} ignores the Motion axis — every duration in the app scales by it`)
        .toMatch(/animation:[^;]*var\(--motion-scale/);
      expect(css, `${w.id} moves a layer without promoting it — that is a repaint per frame`)
        .toMatch(/will-change:\s*transform/);
    }
  });

  it('the gate reads the moving layer, not only the ground', () => {
    for (const w of live)
      expect(stopsOf(w.id).some((s) => s.token === '--primary'),
        `${w.id}: no accent stop measured — the clouds are on a rule the gate does not read`).toBe(true);
  });
});

/**
 * The floor. The ceiling above says a pattern may not drown the ink;
 * this says it must at least be there. Every pattern shipped at a
 * strength the ink cleared fifteen times over — the gate was satisfied
 * and the owner asked how to see the wallpaper. So the strongest stop
 * of each pattern, in each mode, has to move the ground by
 * `WALLPAPER_VISIBLE` ΔE against the plain sidebar.
 */
describe('and every one of them can actually be seen', () => {
  /** The furthest any of a pattern's stops moves the ground, in a mode. */
  const reach = (id: string, mode: 'light' | 'dark'): number => {
    const base = token(body(MODE_CELL[mode]), WALLPAPER_BASE)!.rgb;
    let best = 0;
    for (const stop of stopsOf(id)) {
      const colours = stop.token === '--primary'
        ? THEME_PACKS.map((p) => token(body(packCell(p.id, mode)), '--primary') ?? token(body(MODE_CELL[mode]), '--primary')!)
        : stop.token.startsWith('#') ? [{ rgb: hex(stop.token), alpha: 1 }]
          : [token(body(MODE_CELL[mode]), stop.token)!];
      // The LEAST any accent moves it: a pattern that shows on green and
      // vanishes on blue vanishes for whoever chose blue.
      const least = Math.min(...colours.map((c) => distance(base, mix(base, c.rgb, stop.pct * c.alpha))));
      best = Math.max(best, least);
    }
    return best;
  };

  it('the strongest stop of every pattern moves the ground, in both modes', () => {
    for (const w of WALLPAPERS.filter((x) => x.id !== 'none'))
      for (const mode of ['light', 'dark'] as const)
        expect(reach(w.id, mode), `${w.id}/${mode} is invisible — ΔE ${reach(w.id, mode).toFixed(2)} against the plain chrome`)
          .toBeGreaterThanOrEqual(WALLPAPER_VISIBLE);
  });

  it('and the floor can be missed — a whisper of a stop does not clear it', () => {
    const base = token(body(MODE_CELL.dark), WALLPAPER_BASE)!.rgb;
    const primary = token(body(MODE_CELL.dark), '--primary')!.rgb;
    expect(distance(base, mix(base, primary, 1))).toBeLessThan(WALLPAPER_VISIBLE);
    expect(distance(base, mix(base, primary, 18))).toBeGreaterThanOrEqual(WALLPAPER_VISIBLE);
  });
});

/**
 * The page half. A pattern may show on the content card too, over the
 * page's own colour, around the cards — so the same stops are measured
 * a second time against the page's ground and the page's inks. The
 * muted ink is the one that binds, and in light mode the engine sheet
 * steps it darker on the page ground; that stepped value is what is
 * measured, and the unstepped one is shown to fail, so the step cannot
 * be removed without this noticing.
 */
describe('the page can wear it too, and stays readable', () => {
  const pageInk = (mode: 'light' | 'dark', name: string) => {
    const stepped = mode === 'light'
      ? token(body(`:root:not(.dark)[${WALLPAPER_PAGE_ATTR}]:not([${WALLPAPER_PAGE_ATTR}="none"]) .page-ground`), name)
      : null;
    return stepped ?? token(body(MODE_CELL[mode]), name)!;
  };

  it('both grounds paint the same variable, and the engine names both', () => {
    for (const w of WALLPAPERS.filter((x) => x.id !== 'none')) {
      const paint = paintOf(w.id);
      expect(paint, `${w.id} paints the sidebar colour by name — the page would wear the frame's colour`)
        .not.toMatch(/var\(--sidebar\)/);
      expect(paint, `${w.id} does not paint var(--ground)`).toMatch(/background-color:\s*var\(--ground\)/);
    }
    const engine = engineCss();
    expect(engine).toMatch(/\.chrome-ground\s*\{\s*--ground:\s*var\(--sidebar\)/);
    expect(engine).toMatch(/\.page-ground\s*\{\s*--ground:\s*var\(--background\)/);
  });

  it('the content card is always a page ground, and the page pattern is stamped apart from the frame\'s', () => {
    const shell = readFileSync(join(__dirname, '..', 'shells', 'AppShell.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    expect(shell, 'the content card is not a page ground').toMatch(/\bpage-ground\b/);
    const engine = readFileSync(join(__dirname, 'context.tsx'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    expect(engine, 'the page pattern is never stamped').toMatch(/dataset\.wallpaperPage\s*=\s*theme\.wallpaperPage/);
  });

  it('every pack paints the page ground under its OWN attribute, with the frame\'s stops', () => {
    for (const w of WALLPAPERS.filter((x) => x.id !== 'none')) {
      const page = body(pageGroundOf(w.id)) + body(`${pageGroundOf(w.id)}::before`);
      expect(page, `${w.id} has no rule for the page ground`).not.toBe('');
      // The same declarations: one rule, two names. A page ground that
      // paints different stops would be a second pattern the frame's
      // measurements say nothing about.
      expect(page).toBe(paintOf(w.id));
    }
  });

  it('every declared stop clears AA under the page inks, in both modes, under every accent', () => {
    let measured = 0;
    for (const mode of ['light', 'dark'] as const) {
      const base = token(body(MODE_CELL[mode]), WALLPAPER_PAGE_BASE)!.rgb;
      for (const inkName of WALLPAPER_PAGE_INKS) {
        const ink = pageInk(mode, inkName).rgb;
        for (const w of WALLPAPERS.filter((x) => x.id !== 'none')) {
          for (const stop of stopsOf(w.id)) {
            const colours = stop.token === '--primary'
              ? THEME_PACKS.map((p) => [p.id, token(body(packCell(p.id, mode)), '--primary') ?? token(body(MODE_CELL[mode]), '--primary')!] as const)
              : stop.token.startsWith('#') ? [['—', { rgb: hex(stop.token), alpha: 1 }] as const]
                : [['—', token(body(MODE_CELL[mode]), stop.token)!] as const];
            for (const [pack, c] of colours) {
              measured++;
              expect(contrastRatio(ink, mix(base, c.rgb, stop.pct * c.alpha)),
                `${inkName} over ${w.id} ${stop.token}@${stop.pct}% (${pack}/${mode}) on the page`)
                .toBeGreaterThanOrEqual(WALLPAPER_AA);
            }
          }
        }
      }
    }
    expect(measured, 'nothing was measured').toBeGreaterThan(40);
  });

  it('and the light step is load-bearing — the unstepped muted ink fails under the strongest stop', () => {
    const base = token(body(MODE_CELL.light), WALLPAPER_PAGE_BASE)!.rgb;
    const raw = token(body(MODE_CELL.light), '--muted-foreground')!.rgb;
    const stepped = pageInk('light', '--muted-foreground').rgb;
    expect(stepped, 'no light step on the page ground — the test above is measuring the raw ink').not.toEqual(raw);
    let worst = Infinity;
    for (const w of WALLPAPERS.filter((x) => x.id !== 'none'))
      for (const stop of stopsOf(w.id).filter((s) => s.token === '--primary'))
        for (const p of THEME_PACKS) {
          const c = token(body(packCell(p.id, 'light')), '--primary') ?? token(body(MODE_CELL.light), '--primary')!;
          worst = Math.min(worst, contrastRatio(raw, mix(base, c.rgb, stop.pct * c.alpha)));
        }
    expect(worst, 'the raw muted ink clears every stop — the step is decoration').toBeLessThan(WALLPAPER_AA);
  });
});
