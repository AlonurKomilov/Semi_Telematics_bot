/**
 * A pack's CSS has to be what its seed produces.
 *
 * This is the link that was missing. `themeBoot.test.ts` already keeps
 * the boot script's hand-written accent list in step with the registry,
 * and chrome.test.ts asserts the array and demands a CSS block per
 * accent per mode. Nothing checked that the VALUES in those blocks bear
 * any relation to anything — they were six hand-tuned cells, and a
 * seventh could have been any colour at all.
 *
 * With the catalogue, a pack is a name and a seed, and everything else
 * is `derivePalette`. So the CSS becomes generated output that happens
 * to be committed, and this guard is what makes "committed" safe: paste
 * a value the derivation would not produce and the suite says so.
 *
 * Why committed at all, rather than applied at runtime: because it keeps
 * every other colour guard alive. `colour.test.ts` and the twelve chrome
 * guards read index.css off disk. A pack that exists only at runtime is
 * invisible to all of them.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { THEME_PACKS, PACK_TOKENS, packById } from './themePacks';
import { derivePalette } from './palette';
import { oklchToSrgb, parseHex, toHex, type RGB } from './contrast';

const CSS = readFileSync(join(__dirname, '..', 'index.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

/** `:root` is several blocks in this file; merge in source order. */
function body(selector: string): string {
  let out = '';
  for (const m of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g))
    if (m[1].trim().replace(/\s+/g, ' ') === selector) out += m[2];
  return out;
}

function token(src: string, name: string): RGB | null {
  const m = new RegExp(`${name}:\\s*oklch\\(([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)\\s*\\)`).exec(src);
  return m ? oklchToSrgb(+m[1], +m[2], +m[3]).rgb : null;
}

const dE = (a: RGB, b: RGB) => {
  const lab = (rgb: RGB): [number, number, number] => {
    const inv = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const [r, g, bl] = rgb.map(inv);
    const X = 0.4124564 * r + 0.3575761 * g + 0.1804375 * bl;
    const Y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * bl;
    const Z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * bl;
    const f = (t: number) => (t > (6 / 29) ** 3 ? Math.cbrt(t) : t / (3 * (6 / 29) ** 2) + 4 / 29);
    const [fx, fy, fz] = [f(X / 0.95047), f(Y), f(Z / 1.08883)];
    return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
  };
  const [L1, a1, b1] = lab(a), [L2, a2, b2] = lab(b);
  return Math.hypot(L1 - L2, a1 - a2, b1 - b2);
};

/** Where a pack's tokens live. The FIRST pack is the base — its values
 *  are in `:root` / `.dark` themselves, with no attribute block, because
 *  it is what an unstamped document paints. */
const cellFor = (packIndex: number, id: string, mode: 'light' | 'dark') =>
  packIndex === 0
    ? (mode === 'light' ? ':root' : '.dark')
    : (mode === 'light' ? `:root:not(.dark)[data-accent="${id}"]` : `.dark[data-accent="${id}"]`);

const CANVAS = {
  light: token(body(':root'), '--background')!,
  dark: token(body('.dark'), '--background')!,
};

describe('every pack is its own seed', () => {
  it('finds the canvases it measures against', () => {
    // If this fails everything below is comparing against undefined and
    // would otherwise pass by measuring nothing.
    expect(CANVAS.light, ':root has no --background').not.toBeNull();
    expect(CANVAS.dark, '.dark has no --background').not.toBeNull();
    expect(THEME_PACKS.length).toBeGreaterThan(0);
  });

  for (const [i, pack] of THEME_PACKS.entries()) {
    for (const mode of ['light', 'dark'] as const) {
      it(`${pack.id} / ${mode} matches derivePalette(${pack.seed[mode]})`, () => {
        const cell = cellFor(i, pack.id, mode);
        const src = body(cell);
        expect(src, `no CSS block for ${cell} — a pack without a block paints the base`)
          .not.toBe('');

        const pal = derivePalette({ mode, canvas: toHex(CANVAS[mode]), brand: pack.seed[mode] })!;
        expect(pal, `${pack.id}: seed ${pack.seed[mode]} did not parse`).not.toBeNull();

        for (const name of PACK_TOKENS) {
          const shipped = token(src, name);
          expect(shipped, `${cell} does not declare ${name}`).not.toBeNull();
          const d = dE(shipped!, parseHex(pal[name])!);
          // 3 is above the worst measured (2.0, light green's
          // --primary-text) and well below anything a person reads as a
          // different colour. It is a drift bound, not a licence: a
          // value that needs more than this is not the seed's value.
          expect(
            d,
            `${cell} ${name}: ships ${toHex(shipped!)}, seed gives ${pal[name]} (ΔE ${d.toFixed(1)})`,
          ).toBeLessThan(3);
        }
      });
    }
  }
});

describe('the catalogue and the stylesheet agree', () => {
  it('has no accent block that no pack claims', () => {
    // The other direction: a pack deleted from the list but left in the
    // CSS keeps painting for anyone whose stored value still names it.
    const orphans: string[] = [];
    for (const m of CSS.matchAll(/\[data-accent="([^"]+)"\]/g))
      if (!packById(m[1])) orphans.push(m[1]);
    expect([...new Set(orphans)], 'CSS blocks for packs that no longer exist').toEqual([]);
  });

  it('gives every pack a swatch in both modes', () => {
    // The picker paints its dots from these, and a pack with no swatch
    // is a blank chip — the kind of miss that used to come with adding
    // an accent by hand.
    for (const pack of THEME_PACKS) {
      const re = new RegExp(`--swatch-accent-${pack.id}\\s*:`, 'g');
      const count = (CSS.match(re) || []).length;
      expect(count, `--swatch-accent-${pack.id} appears ${count} times, expected one per theme`)
        .toBeGreaterThanOrEqual(2);
    }
  });

  it('keeps ids usable as an attribute value and a CSS name', () => {
    for (const pack of THEME_PACKS) {
      expect(pack.id, `${pack.id} is not a safe custom-property suffix`).toMatch(/^[a-z][a-z0-9-]*$/);
      expect(pack.label.trim(), `${pack.id} has no label`).not.toBe('');
    }
    expect(new Set(THEME_PACKS.map((p) => p.id)).size, 'duplicate pack id').toBe(THEME_PACKS.length);
  });
});
