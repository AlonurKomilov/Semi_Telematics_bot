/**
 * The chrome we do not own, and the one rule that makes overriding it
 * safe.
 *
 * Leaflet and sonner inject their own stylesheets at runtime — Leaflet's
 * arrives from a CDN, so it is outside the build, outside `dist/` and
 * outside every other guard in this repo. Nothing here can read those
 * files. What it CAN hold is our side of the contract, and our side has
 * exactly one law, written in `index.css` above the block it governs:
 *
 *   never write a vendor override at equal specificity.
 *
 * At equal specificity the winner is document order, and the order is
 * not stable: sonner is imported before `index.css`, which under `vite
 * dev` puts the app second (app wins) and in the production build puts
 * it first (sonner wins). An override written that way looks fixed in
 * dev and ships broken — which is the single most expensive shape of bug
 * this file can prevent, because it cannot be seen locally at all.
 *
 * So every selector below must out-specify the vendor's own. That is
 * cheap to check and it is the whole rule.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const CSS = readFileSync(join(__dirname, '..', '..', 'index.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

/** Every selector in the sheet that names a vendor's own class. */
const VENDOR = /(^|\})\s*([^{}]*(?:leaflet|sonner)[^{}]*)\{/gi;

const selectorsOf = (list: string) =>
  list.split(',').map((s) => s.trim()).filter(Boolean);

/**
 * The middle number of a selector's specificity: classes, attributes and
 * pseudo-CLASSES.
 *
 * Not a count of compounds, which is what this measured first — and it
 * failed on the sheet's own `[data-sonner-toaster][data-sonner-toaster]`,
 * a selector that is one compound and (0,2,0) because repeating an
 * attribute is the cheapest legal way to outrank a vendor when there is
 * no ancestor to add. The law is about specificity; counting compounds
 * was a proxy that disagreed with it on the first real case.
 *
 * `::before` is a pseudo-ELEMENT and counts in the last column, so the
 * lookbehind keeps it out.
 */
const specificity = (sel: string) =>
  (sel.match(/\.[-\w]+|\[[^\]]*\]|(?<!:):[-\w]+(?:\([^)]*\))?/g) ?? []).length;

describe('vendor chrome is overridden the only way that survives a build', () => {
  const found = [...CSS.matchAll(VENDOR)].flatMap((m) => selectorsOf(m[2]));

  it('finds the overrides to check', () => {
    expect(found.length, 'no vendor selectors in index.css — this checks nothing')
      .toBeGreaterThan(10);
  });

  it('never at equal specificity — a tie is decided by whichever sheet loaded first', () => {
    const flat = found.filter((s) => specificity(s) < 2);
    expect(flat, 'a vendor override at (0,1,0): it wins in `vite dev` and '
      + 'loses in the production build, or the other way round, depending on which '
      + 'stylesheet the bundler put first')
      .toEqual([]);
  });

  it('and the scan can fail', () => {
    expect(specificity('.leaflet-container')).toBe(1);
    expect(specificity(':root .leaflet-container')).toBe(2);
    expect(specificity('[data-sonner-toaster] [data-sonner-toast]')).toBe(2);
    // One compound, still safe: repeating the attribute is how you
    // outrank a vendor that gave you no ancestor to hang off.
    expect(specificity('[data-sonner-toaster][data-sonner-toaster]')).toBe(2);
    // A pseudo-element is not a class.
    expect(specificity('.leaflet-tooltip-left::before')).toBe(1);
  });
});

describe('what we reach on the two vendors', () => {
  /** Each entry: what it is, and the selector that must exist for it. */
  const REACHED: ReadonlyArray<readonly [string, RegExp]> = [
    ['the map zoom control takes the popover colour', /\.leaflet-bar a[,\s{]/],
    ['the map attribution takes the app palette', /\.leaflet-control-attribution\s*\{/],
    ['the map backdrop is a token, not #ddd', /:root \.leaflet-container\s*\{/],
    ["the map's zoom rides the Motion axis", /\.leaflet-zoom-animated\s*\{/],
    ['the toaster takes the typeface', /\[data-sonner-toaster\]\[data-sonner-toaster\]\s*\{/],
  ];

  for (const [what, sel] of REACHED) {
    it(what, () => {
      expect(CSS, `${what} — the rule is gone from index.css`).toMatch(sel);
    });
  }

  it('the toaster draws OUR glyphs, not its own five', () => {
    // Sonner ships its own check / cross / warning / info / spinner. Left
    // alone they are a second icon vocabulary on the one surface every
    // page shows, which is the rule the icon door exists to enforce —
    // and no stylesheet can fix it, because they are SVGs in its
    // JavaScript. The only reach is the `icons` prop.
    const main = readFileSync(join(__dirname, '..', '..', 'main.tsx'), 'utf8');
    expect(main, 'the Toaster stopped passing `icons` — sonner is drawing its own')
      .toMatch(/<Toaster[\s\S]*?icons=\{\{/);
    for (const tone of ['success', 'error', 'warning', 'info', 'loading']) {
      expect(main, `the toaster has no glyph for "${tone}"`).toMatch(new RegExp(`${tone}:\\s*<`));
    }
  });
});
