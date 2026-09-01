/**
 * The public apply form derives its whole appearance from colours a
 * carrier typed into a picker. These guards are what stop that being a
 * promise.
 *
 * Every assertion below is a sweep over the sRGB cube, not a handful of
 * brand colours, and that distinction is the reason this file exists.
 * The values it replaced were spot-checked — "measured across eight
 * brand colours", "across seven brand colours and four surfaces" — and
 * every one of them held on its samples while failing on a third to
 * four-fifths of everything a customer could actually enter.
 *
 * Two numbers recur and are not defects:
 *
 *   ~1.1%  of surfaces admit NO text at 4.5:1 from either near-black or
 *          white. Since those are the luminance extremes, no colour
 *          does. The floor there is 4.45:1, and `surfaceContrastWeak`
 *          is what tells the recruiter.
 *
 *   ~40%   of HEADER colours admit no AA text over an arbitrary photo at
 *          the current 0.78 scrim. That is opacity, not arithmetic — see
 *          the table in heroHeaderStyle.
 */
import { describe, it, expect } from 'vitest';
import {
  surfaceThemeStyle, brandTintStyle, onColorStyle, heroHeaderStyle,
  surfaceContrastWeak, readableTextOn, OUR_HAIRLINE,
} from './theme';
import {
  parseHex, toHex, contrastRatio, readableOn, over, AA_TEXT, type RGB,
} from '../../../mods';

const cube = (step: number): RGB[] => {
  const out: RGB[] = [];
  for (let r = 0; r < 256; r += step)
    for (let g = 0; g < 256; g += step)
      for (let b = 0; b < 256; b += step) out.push([r / 255, g / 255, b / 255]);
  return out;
};
const CUBE = cube(16);          // 4,096 carrier colours
const P = (v: unknown) => parseHex(String(v))!;
const H = (c: RGB) => toHex(c);
/** Surfaces where no text can reach AA — a fact about the colour, not us. */
const impossible = (c: RGB) => contrastRatio(readableOn(c), c) < AA_TEXT;

describe('surfaceThemeStyle', () => {
  it('refuses a colour it cannot parse', () => {
    for (const bad of ['', 'red', '#12345', 'rgb(1,2,3)', '#gggggg'])
      expect(surfaceThemeStyle(bad), JSON.stringify(bad)).toBeUndefined();
    expect(surfaceThemeStyle(undefined)).toBeUndefined();
    // An unparseable colour used to be pasted straight into the custom
    // properties, where the browser dropped each one and the form
    // rendered half-themed rather than not themed.
    expect(surfaceThemeStyle('#3366aa')).toBeDefined();
  });

  it('puts body text at AA on every surface that allows it', () => {
    const stuck: string[] = [];
    for (const s of CUBE) {
      const st = surfaceThemeStyle(H(s)) as Record<string, string>;
      const got = contrastRatio(P(st['--foreground']), P(st['--background']));
      if (got >= AA_TEXT - 1e-9) continue;
      // Below AA is only allowed where nothing could have done better.
      expect(impossible(s), `${H(s)} had a readable option and missed it`).toBe(true);
      expect(got, `${H(s)}`).toBeGreaterThan(4.4);
      stuck.push(H(s));
    }
    expect(stuck.length / CUBE.length, 'the unreachable band grew').toBeLessThan(0.02);
  });

  it('puts secondary text at AA on EVERY surface, with no exceptions', () => {
    // No band here, because --muted-foreground is not pinned to an
    // extreme: it is clamped, so it can always be moved far enough. 56
    // sites of this form read it, and the 55% wash it replaced was under
    // AA on 84% of surfaces.
    for (const s of CUBE) {
      const st = surfaceThemeStyle(H(s)) as Record<string, string>;
      expect(contrastRatio(P(st['--muted-foreground']), P(st['--muted'])),
        `${H(s)}: --muted-foreground on --muted`).toBeGreaterThanOrEqual(AA_TEXT - 1e-9);
    }
  });

  it('keeps every boundary at least as visible as our own', () => {
    // Not WCAG 1.4.11's 3:1 — deliberately. Our light theme's --border
    // and --input sit at 1.26:1, and a public page whose fields are
    // outlined twice as hard as the dashboard's reads as a different
    // product. This matches what we ship; the bare 16% wash was under it
    // on 86% of surfaces, which is a boundary nobody can see.
    for (const s of CUBE) {
      const st = surfaceThemeStyle(H(s)) as Record<string, string>;
      const bg = P(st['--background']);
      for (const t of ['--border', '--input'])
        expect(contrastRatio(P(st[t]), bg), `${H(s)}: ${t}`)
          .toBeGreaterThanOrEqual(OUR_HAIRLINE - 1e-9);
    }
  });
});

describe('brandTintStyle', () => {
  it('refuses a brand colour it cannot parse', () => {
    expect(brandTintStyle('', '#ffffff')).toBeUndefined();
    expect(brandTintStyle('nope', '#ffffff')).toBeUndefined();
  });

  it('keeps the accent legible AS TEXT on the surface', () => {
    for (const surface of ['#ffffff', '#101010', '#f4efe6', '#0a4d8c']) {
      const sg = parseHex(surface)!;
      for (const b of CUBE) {
        const st = brandTintStyle(H(b), surface) as Record<string, string>;
        expect(contrastRatio(P(st['--primary-text']), sg),
          `brand ${H(b)} on ${surface}`).toBeGreaterThanOrEqual(AA_TEXT - 1e-9);
      }
    }
  });

  it('keeps the button label legible at rest AND on hover', () => {
    // The label does not change when the pointer lands, so the hover
    // fill has to move AWAY from it. Always darkening — which is what
    // this did — walks a pale fill toward a dark label and put 17.2% of
    // brand colours under AA on hover after passing at rest.
    for (const b of CUBE) {
      const st = brandTintStyle(H(b), '#ffffff') as Record<string, string>;
      const label = P(st['--primary-foreground']);
      const rest = contrastRatio(label, b);
      const hover = contrastRatio(label, P(st['--primary-hover']));
      expect(hover, `brand ${H(b)}: hover must not be worse than rest`)
        .toBeGreaterThanOrEqual(rest - 1e-9);
      if (rest < AA_TEXT) expect(impossible(b), `${H(b)}`).toBe(true);
    }
  });

  it('still declares every token an element-scoped tint has to carry', () => {
    // A token derived at :root resolves against :root's --primary, so it
    // does NOT follow a tint applied to a subtree. Three separate
    // regressions came from dropping one of these as a "duplicate".
    const st = brandTintStyle('#c2410c', '#ffffff') as Record<string, string>;
    for (const t of ['--primary', '--ring', '--primary-hover', '--primary-text', '--primary-foreground'])
      expect(st[t], `${t} missing from the tint`).toBeTruthy();
  });
});

describe('onColorStyle', () => {
  it('keeps dimmed text at AA on every carrier colour', () => {
    for (const c of CUBE) {
      const st = onColorStyle(H(c)) as Record<string, string>;
      expect(contrastRatio(P(st['--muted-foreground']), c), H(c))
        .toBeGreaterThanOrEqual(AA_TEXT - 1e-9);
    }
    expect(onColorStyle('bogus')).toBeUndefined();
  });
});

describe('heroHeaderStyle', () => {
  it('gets as close to AA as the scrim physically allows', () => {
    // A RATCHET against the optimum, not a floor. The ground here is the
    // scrim over a photograph nobody has seen, so both extremes have to
    // be satisfied and for many header colours no colour does. What is
    // in our control is the gap to the best that exists.
    const greys: RGB[] = [];
    for (let v = 0; v < 256; v += 2) greys.push([v / 255, v / 255, v / 255]);
    let failed = 0, unreachable = 0;
    for (const tint of CUBE) {
      const gs: RGB[] = ([[1, 1, 1], [0, 0, 0]] as RGB[]).map((img) => over(tint, 0.78, img));
      const worstOn = (c: RGB) => Math.min(...gs.map((g) => contrastRatio(c, g)));
      const st = heroHeaderStyle('x.jpg', H(tint)) as Record<string, string>;
      if (worstOn(P(st['--muted-foreground'])) < AA_TEXT) failed++;
      if (Math.max(...greys.map(worstOn)) < AA_TEXT) unreachable++;
    }
    const gap = (failed - unreachable) / CUBE.length;
    expect(gap, `losing ${(100 * gap).toFixed(1)}% to the derivation, not the scrim`)
      .toBeLessThan(0.03);
  });
});

describe('surfaceContrastWeak', () => {
  it('warns exactly when no text can be read, and never otherwise', () => {
    // Its predecessor was a YIQ band picked by eye: over 32,768 surfaces
    // it warned about 10,880 perfectly readable ones — a third of every
    // colour a recruiter could pick, which is how a warning becomes
    // wallpaper — and stayed silent on 161 that admit no readable text.
    let falseAlarm = 0, missed = 0;
    for (const s of cube(8)) {
      const warned = surfaceContrastWeak(H(s));
      if (warned && !impossible(s)) falseAlarm++;
      if (!warned && impossible(s)) missed++;
    }
    expect(falseAlarm, 'warned about a readable surface').toBe(0);
    expect(missed, 'stayed silent on an unreadable one').toBe(0);
  });
});

describe('readableTextOn', () => {
  it('never returns the worse of the two, and falls back safely', () => {
    for (const s of cube(8)) {
      const got = contrastRatio(P(readableTextOn(H(s))), s);
      const best = Math.max(contrastRatio([1, 1, 1], s), contrastRatio([10 / 255, 10 / 255, 10 / 255], s));
      expect(got, `${H(s)}`).toBeGreaterThanOrEqual(best - 1e-9);
    }
    // The YIQ version this replaced chose white here at 1.37:1.
    expect(readableTextOn('#00ff1b')).toBe('#0a0a0a');
    expect(readableTextOn('garbage')).toBe('#ffffff');
  });
});
