/**
 * A whole palette, and the one thing it cannot derive.
 *
 * `palette.test.ts` already sweeps the seed space and proves what
 * `derivePalette` emits: body text legible on any canvas, the accent
 * legible as text and as a button, the elevation ladder off the canvas.
 * None of that is repeated here.
 *
 * What it does NOT cover is the half the seed is forbidden to touch.
 * The semantic tones follow the MODE, not the canvas, so a canvas chosen
 * against its mode leaves them on a ground nobody measured. That is the
 * only new failure this feature can produce, and it is what this file
 * is about.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fitCanvas, worstTone, paletteTokens, CANVAS_SEED } from './canvas';
import { TONES } from './accent';
import { DERIVED_TOKENS } from './palette';
import { parseHex, oklchToSrgb, toHex, contrastRatio, AA_LARGE } from './contrast';
import { MOD_TOKENS } from '../inject';

const MODES = ['light', 'dark'] as const;
const BRAND = '#ff6a00';

describe('the gap this exists for', () => {
  it('refuses a canvas that makes a tone unreadable, and names the tone', () => {
    // Measured with the shipped tone values — every one of these is a
    // colour nothing else in the tree would have stopped.
    const cases = [
      ['light', '#1a2332', 'info'],   // navy under light tones
      ['light', '#3d2b1f', 'info'],   // brown
      ['dark',  '#ffffff', 'warn'],   // white under dark tones
      ['dark',  '#f5f0e8', 'warn'],   // cream
    ] as const;
    for (const [mode, hex, tone] of cases) {
      const r = fitCanvas(hex, mode);
      expect(r.rgb, `${mode}/${hex} was accepted`).toBeNull();
      expect(r.breaks, `${mode}/${hex} refused without naming the tone`).toBe(tone);
      expect(r.ratio!, `${mode}/${hex} refused at a ratio that passes`).toBeLessThan(AA_LARGE);
    }
  });

  it('accepts a canvas that keeps all four readable', () => {
    // The built-in canvases, which must obviously still be wearable.
    expect(fitCanvas('#ffffff', 'light').rgb, 'white refused in light mode').not.toBeNull();
    expect(fitCanvas('#0a0a0a', 'dark').rgb, 'near-black refused in dark mode').not.toBeNull();
  });

  it('nothing it accepts leaves a tone below the floor', () => {
    // The sweep. A rule that only holds for the four examples above is
    // a rule about those four examples.
    const bad: string[] = [];
    for (const mode of MODES)
      for (let L = 0.05; L <= 0.98; L += 0.03)
        for (const H of [0, 60, 120, 180, 240, 300]) {
          const hex = toHex(oklchToSrgb(L, 0.06, H).rgb);
          const fit = fitCanvas(hex, mode);
          if (!fit.rgb) continue;
          for (const t of Object.keys(TONES[mode])) {
            const [tl, tc, th] = TONES[mode][t];
            const r = contrastRatio(oklchToSrgb(tl, tc, th).rgb, fit.rgb);
            if (r < AA_LARGE) bad.push(`${mode} ${hex} --${t} ${r.toFixed(2)}`);
          }
        }
    expect(bad).toEqual([]);
  });

  it('the sweep really did accept things, or it proved nothing', () => {
    let accepted = 0, refused = 0;
    for (const mode of MODES)
      for (let L = 0.05; L <= 0.98; L += 0.03) {
        const hex = toHex(oklchToSrgb(L, 0.06, 200).rgb);
        if (fitCanvas(hex, mode).rgb) accepted++; else refused++;
      }
    expect(accepted, 'every canvas was refused').toBeGreaterThan(10);
    expect(refused, 'no canvas was ever refused — the gate does not close').toBeGreaterThan(5);
  });

  it('names the worst tone, not merely a failing one', () => {
    const bg = parseHex('#1a2332')!;
    const { name, ratio } = worstTone(bg, 'light');
    for (const t of Object.keys(TONES.light)) {
      const [l, c, h] = TONES.light[t];
      expect(contrastRatio(oklchToSrgb(l, c, h).rgb, bg)).toBeGreaterThanOrEqual(ratio - 1e-9);
    }
    expect(name).toBe('info');
  });
});

describe('what a canvas installs', () => {
  it('the whole palette, not the accent\'s four', () => {
    const r = paletteTokens('#ffffff', BRAND, 'light');
    expect(r.tokens).not.toBeNull();
    expect(Object.keys(r.tokens!).sort()).toEqual([...DERIVED_TOKENS].sort());
    expect(Object.keys(r.tokens!).length).toBeGreaterThan(20);
  });

  it('and every one of them is a token a mod is allowed to write', () => {
    // The injector filters silently; a token outside its list would be
    // dropped and the palette would come out half-applied.
    const r = paletteTokens('#0a0a0a', BRAND, 'dark');
    const outside = Object.keys(r.tokens!).filter((k) => !MOD_TOKENS.includes(k));
    expect(outside, 'the palette names tokens the injector will drop').toEqual([]);
  });

  it('never the tones or the chart ramp — the seed may not reach them', () => {
    const r = paletteTokens('#ffffff', BRAND, 'light');
    const keys = Object.keys(r.tokens!);
    for (const forbidden of ['--ok', '--warn', '--danger', '--info', '--chart-1', '--ring'])
      expect(keys, `the palette wrote ${forbidden}`).not.toContain(forbidden);
  });

  it('carries a refusal through instead of half a palette', () => {
    const r = paletteTokens('#1a2332', BRAND, 'light');
    expect(r.tokens).toBeNull();
    expect(r.breaks).toBe('info');
  });

  it('derives a DIFFERENT palette per mode from the same two colours', () => {
    // The whole reason the seed is stored and the tokens are not.
    const light = paletteTokens('#ffffff', BRAND, 'light').tokens!;
    const dark = paletteTokens('#0a0a0a', BRAND, 'dark').tokens!;
    expect(light['--card']).not.toBe(dark['--card']);
    expect(light['--foreground']).not.toBe(dark['--foreground']);
  });

  it('refuses a brand that is not a colour, rather than throwing', () => {
    expect(() => paletteTokens('#ffffff', 'not-a-colour', 'light')).not.toThrow();
    expect(paletteTokens('#ffffff', 'not-a-colour', 'light').tokens).toBeNull();
  });
});


describe('the canvas seeds are the ones the stylesheet paints', () => {
  const CSS = readFileSync(join(__dirname, '..', '..', 'index.css'), 'utf8');

  /** `:root` and `.dark` are each several blocks; merge in source order
   *  and take the FIRST --background, which is the live one. */
  const declared = (block: ':root' | '.dark'): string | null => {
    let body = '';
    for (const m of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g))
      if (m[1].trim().replace(/\s+/g, ' ') === block) body += m[2];
    const m = /--background:\s*oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\)/.exec(body);
    return m ? toHex(oklchToSrgb(+m[1], +m[2], +m[3]).rgb) : null;
  };

  it('finds the blocks it reads, so a rename fails loudly', () => {
    expect(declared(':root'), ':root declares no --background').not.toBeNull();
    expect(declared('.dark'), '.dark declares no --background').not.toBeNull();
  });

  it('matches index.css in both modes', () => {
    // The picker opens on these. Drift and it opens on a colour the page
    // is not painting, which reads as the control being broken.
    expect(CANVAS_SEED.light, 'the light seed drifted from --background').toBe(declared(':root'));
    expect(CANVAS_SEED.dark, 'the dark seed drifted from --background').toBe(declared('.dark'));
  });

  it('and each is wearable in its own mode, or the default is refused', () => {
    for (const mode of MODES)
      expect(fitCanvas(CANVAS_SEED[mode], mode).rgb, `${mode}'s own canvas is refused`).not.toBeNull();
  });
});
