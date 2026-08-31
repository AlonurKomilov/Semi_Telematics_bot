/**
 * The one test that matters here is the first: fed our own canvas and
 * accent, the derivation has to hand back the themes we already ship.
 *
 * A rule that cannot regenerate the design it was measured from is not
 * the rule that design was using — it is a rule that happens to produce
 * something plausible, which is exactly what a themable product cannot
 * afford. Everything else below checks that the same rules stay legible
 * on seeds nobody has looked at.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { derivePalette, DERIVED_TOKENS } from './palette';
import {
  oklchToSrgb, over, parseHex, toHex, contrastRatio, type RGB,
} from './contrast';

const CSS = readFileSync(join(__dirname, '..', 'index.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

/**
 * `:root` is FOUR separate blocks in index.css — fonts, then colour, then
 * the mode swatches, then the size axes. Merging them in source order is
 * what the cascade does; reading only the first finds two font stacks and
 * no colour at all, and a guard built on that would pass by measuring
 * nothing.
 */
function cell(selector: string): string {
  let out = '';
  for (const m of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g))
    if (m[1].trim().replace(/\s+/g, ' ') === selector) out += m[2];
  return out;
}

/** Resolve a token, compositing any alpha over the ground it sits on. */
function token(body: string, name: string, ground?: RGB): RGB | null {
  const m = new RegExp(
    `${name}:\\s*oklch\\(([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)(?:\\s*/\\s*([\\d.]+)%?)?\\s*\\)`,
  ).exec(body);
  if (!m) return null;
  const rgb = oklchToSrgb(+m[1], +m[2], +m[3]).rgb;
  if (m[4] === undefined) return rgb;
  const a = +m[4] > 1 ? +m[4] / 100 : +m[4];
  return ground ? over(rgb, a, ground) : rgb;
}

/** CIELab ΔE — "do these look the same", which contrast cannot answer. */
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

const CELLS = [
  { mode: 'light' as const, sel: ':root' },
  { mode: 'dark' as const, sel: '.dark' },
];

describe('regenerating the themes we ship', () => {
  for (const { mode, sel } of CELLS) {
    it(`reproduces ${mode} from its own canvas and accent`, () => {
      const body = cell(sel);
      const canvas = token(body, '--background');
      const brand = token(body, '--primary');
      expect(canvas, `${sel} has no --background — did index.css move?`).not.toBeNull();
      expect(brand, `${sel} has no --primary`).not.toBeNull();

      const pal = derivePalette({ mode, canvas: toHex(canvas!), brand: toHex(brand!) })!;
      expect(pal).not.toBeNull();

      const gaps: string[] = [];
      const all: number[] = [];
      for (const name of DERIVED_TOKENS) {
        // The sidebar plane's tokens sit on the sidebar, not the page,
        // so an alpha value has to composite over the right ground.
        const ground = name.startsWith('--sidebar')
          ? token(body, '--sidebar', canvas!) ?? canvas! : canvas!;
        const authored = token(body, name, ground);
        if (!authored) continue;   // not declared in this cell
        const d = dE(authored, parseHex(pal[name])!);
        all.push(d);
        if (d > 5) gaps.push(`${name}: ${toHex(authored)} → ${pal[name]} (ΔE ${d.toFixed(1)})`);
      }
      // Enough tokens actually compared — a broken parser returning
      // nothing would otherwise pass this whole test in silence.
      expect(all.length, 'almost nothing was compared').toBeGreaterThan(18);
      all.sort((a, b) => a - b);
      const median = all[Math.floor(all.length / 2)];
      // 5 is roughly where a colour difference stops being subtle. The
      // measured result is far inside it: light median 0.0 worst 3.0,
      // dark median 0.3 worst 2.0.
      expect(gaps, `derivation drifted from the shipped ${mode} theme:\n  ${gaps.join('\n  ')}`)
        .toEqual([]);
      expect(median, `${mode} median ΔE`).toBeLessThan(1);
    });
  }
});

describe('the seed space at large', () => {
  const SEEDS: { mode: 'dark' | 'light'; canvas: string; brand: string }[] = [];
  for (let i = 0; i < 256; i += 32)
    for (let j = 0; j < 256; j += 64)
      for (const mode of ['dark', 'light'] as const)
        SEEDS.push({
          mode,
          canvas: toHex([i / 255, j / 255, ((i + j) % 256) / 255]),
          brand: toHex([j / 255, ((i * 2) % 256) / 255, i / 255]),
        });

  it('never returns half a palette', () => {
    for (const s of SEEDS) {
      const pal = derivePalette(s)!;
      for (const t of DERIVED_TOKENS)
        expect(pal[t], `${t} missing for ${s.canvas}`).toMatch(/^#[0-9a-f]{6}$/);
    }
    // Half a theme applied over the other half is worse than none.
    expect(derivePalette({ mode: 'dark', canvas: 'nope', brand: '#123456' })).toBeNull();
    expect(derivePalette({ mode: 'dark', canvas: '#123456', brand: '' })).toBeNull();
  });

  it('keeps body text legible on any canvas', () => {
    let stuck = 0;
    for (const s of SEEDS) {
      const pal = derivePalette(s)!;
      const r = contrastRatio(parseHex(pal['--foreground'])!, parseHex(pal['--background'])!);
      // No band at all, and that is the point of pickInk. The shipped
      // inks alone bottom out at 4.3552 and miss AA on 3.19% of
      // canvases; escalating to the extremes where they will not do
      // buys back contrast.ts's 4.5826 guarantee without making every
      // other page harsher than the one we designed.
      expect(r, `${s.canvas}`).toBeGreaterThanOrEqual(4.5 - 1e-9);
      if (r < 4.5) stuck++;
    }
    expect(stuck, 'a canvas fell under AA — pickInk stopped escalating').toBe(0);
  });

  it('keeps the accent legible as text and as a button', () => {
    for (const s of SEEDS) {
      const pal = derivePalette(s)!;
      expect(
        contrastRatio(parseHex(pal['--primary-text'])!, parseHex(pal['--background'])!),
        `--primary-text on ${s.canvas}`,
      ).toBeGreaterThanOrEqual(4.5 - 1e-9);
      // The label must hold on the rest fill AND on hover, since hover
      // moves the fill and not the label.
      const label = parseHex(pal['--primary-foreground'])!;
      const rest = contrastRatio(label, parseHex(pal['--primary'])!);
      const hover = contrastRatio(label, parseHex(pal['--primary-hover'])!);
      expect(hover, `hover must not be worse than rest for ${s.brand}`)
        .toBeGreaterThanOrEqual(rest - 1e-9);
      expect(rest, `label on ${s.brand}`).toBeGreaterThanOrEqual(4.5 - 1e-9);
    }
  });

  it('keeps the dark elevation ladder off the canvas', () => {
    // Light's cards deliberately sit AT the canvas — elevation is carried
    // by shadow there — so this is a dark-only property. Asserting it in
    // both modes is how someone "fixes" light into a flat grey soup.
    for (const s of SEEDS.filter((x) => x.mode === 'dark')) {
      const pal = derivePalette(s)!;
      const bg = parseHex(pal['--background'])!;
      for (const t of ['--card', '--popover', '--muted']) {
        const d = dE(parseHex(pal[t])!, bg);
        // Only where there is room: a canvas already at the top of the
        // range cannot rise off itself.
        if (contrastRatio([1, 1, 1], bg) < 1.6) continue;
        expect(d, `${t} collapsed into the canvas at ${s.canvas}`).toBeGreaterThan(1.5);
      }
    }
  });
});

describe('what the seed is not allowed to touch', () => {
  it('never reaches the tones, the ramp, or the structural axes', () => {
    // The seed's blast radius, asserted rather than trusted. A red that
    // means "danger" cannot also be a carrier's brand red; chart series
    // separation is a property of the whole set and cannot be derived
    // slot by slot; radius and the size axes are their own axes.
    const forbidden = [
      '--ok', '--warn', '--danger', '--info',
      '--ok-foreground', '--warn-foreground', '--danger-foreground', '--info-foreground',
      '--ok-bg', '--ok-bd', '--warn-bg', '--warn-bd',
      '--danger-bg', '--danger-bd', '--info-bg', '--info-bd',
      '--destructive', '--destructive-text', '--destructive-foreground',
      '--chart-1', '--chart-2', '--chart-3', '--chart-4', '--chart-5',
      '--swatch-accent-blue', '--swatch-accent-purple', '--swatch-accent-green',
      '--swatch-mode-dark', '--swatch-mode-light',
      '--radius', '--sidebar-w', '--font-sans', '--font-heading',
      '--size-text', '--size-control', '--size-layout', '--size-panel',
      '--pin-shadow-left', '--pin-shadow-right',
    ];
    const pal = derivePalette({ mode: 'dark', canvas: '#101014', brand: '#c2410c' })!;
    for (const t of forbidden)
      expect(Object.keys(pal), `the seed reached ${t}`).not.toContain(t);
    expect(Object.keys(pal).sort()).toEqual([...DERIVED_TOKENS].sort());
  });

  it('leaves --ring to the cascade', () => {
    // --ring is `var(--primary)` on :root. A seed landing on :root is
    // substituted there, so the ring follows for free — setting it here
    // would be a second declaration to keep in sync for no gain. This
    // only holds while the seed lands on the ROOT element; an
    // element-scoped tint has to set it explicitly, which is what
    // features/applications/public/theme.ts does and documents.
    expect(DERIVED_TOKENS).not.toContain('--ring');
    expect(CSS, '--ring stopped being derived — the seed must set it now')
      .toMatch(/--ring:\s*var\(--primary\)/);
  });
});
