/**
 * Guards for the colour a customer picks.
 *
 * Everything else in this folder measures colours WE authored, which
 * means every one of them is checked against a finite table somebody
 * looked at. This file's subject does not exist until a person types it,
 * so the guards here sweep the wheel instead of listing examples.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  ACCENT_BAND, TONES, TONE_FLOOR, STATEFUL_TONES, fitAccent, accentTokens,
  type AccentMode,
} from './accent';
import {
  oklchToSrgb, srgbToOklch, parseHex, toHex, distance, contrastRatio,
  AA_TEXT, AA_LARGE, type RGB,
} from './contrast';
import { THEME_PACKS } from '../catalogue';

const CSS = readFileSync(resolve(__dirname, '../../index.css'), 'utf8');
const MODES: AccentMode[] = ['light', 'dark'];
const tone = (mode: AccentMode, name: string): RGB => {
  const [L, C, H] = TONES[mode][name];
  return oklchToSrgb(L, C, H).rgb;
};

describe('the tone table is the one that ships', () => {
  /**
   * `:root` holds the light tones and `.dark` the dark ones — but each
   * is several blocks in this file, so they are merged in source order
   * exactly as the cascade merges them. Reading only the first block is
   * how this reader returned null for a token that is plainly declared.
   *
   * The FIRST match wins after merging, which is the light/dark pair;
   * the later restatements are the print reset and the tone layer, and
   * they carry the same values.
   */
  const declared = (block: '.dark' | ':root', name: string): [number, number, number] | null => {
    let body = '';
    for (const m of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g))
      if (m[1].trim().replace(/\s+/g, ' ') === block) body += m[2];
    const m = new RegExp(`--${name}:\\s*oklch\\(([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)\\s*\\)`).exec(body);
    return m ? [+m[1], +m[2], +m[3]] : null;
  };

  it('finds the blocks it reads, so a rename fails loudly', () => {
    expect(declared(':root', 'ok'), ':root has no --ok').not.toBeNull();
    expect(declared('.dark', 'ok'), '.dark has no --ok').not.toBeNull();
  });

  for (const mode of MODES)
    for (const name of Object.keys(TONES.light))
      it(`${mode} --${name} matches index.css`, () => {
        const css = declared(mode === 'light' ? ':root' : '.dark', name);
        expect(css, `--${name} not declared in ${mode}`).not.toBeNull();
        expect(TONES[mode][name]).toEqual(css);
      });
});

/** How far off `ACCENT_BAND` a seed may sit and still read as family. */
const BAND_TOLERANCE = 0.005;

describe('the band is where the packs already live', () => {
  it('every curated seed sits on its mode band', () => {
    const off: string[] = [];
    for (const p of THEME_PACKS)
      for (const mode of MODES) {
        const L = srgbToOklch(parseHex(p.seed[mode])!).L;
        if (Math.abs(L - ACCENT_BAND[mode]) > BAND_TOLERANCE) off.push(`${p.id}/${mode} L=${L.toFixed(3)}`);
      }
    // Green/dark is the one exception left. Green/LIGHT used to be the
    // other, at 0.480, on reasoning that stopped holding when `--ok`
    // moved — it is back on the band with its siblings. Listing the
    // survivor rather than widening the tolerance keeps the band a real
    // claim.
    //
    // 0.651, and it used to read 0.650. The colour did not move: the
    // seed is 0.6507 and was 0.6504, either side of where `toFixed(3)`
    // rounds. Hue 135 offers no 8-bit green between 0.648 and 0.651, so
    // there is no hex that keeps the old string — this is a rounding
    // boundary written down, not a lightness change.
    expect(off).toEqual(['green/dark L=0.651']);
  });
});

describe('what the shipped packs actually measure', () => {
  /**
   * A characterisation test, not an approval.
   *
   * Every curated seed is recorded against its nearest tone so that any
   * change to a pack, or to a tone, has to come through here.
   *
   * Blue/light's 6.49 is the only one left below `TONE_FLOOR`, and it
   * is measured against `--info` — a tone that carries no state, which
   * is why the gate does not judge against it. See STATEFUL_TONES, and
   * the test below that says so in a number.
   *
   * Green used to be the sharper half and is not any more. It sat at
   * 6.09 from `--ok` on hue 142 while imposing a floor of 10 on anyone
   * picking their own colour — the one pack that failed its own gate.
   * The hue moved to 135 (owner's decision, 2026-09-06) and it now
   * measures 10.13 light and 14.89 dark.
   */
  it('records every seed against its nearest tone', () => {
    const table: string[] = [];
    let worst = Infinity;
    for (const p of THEME_PACKS)
      for (const mode of MODES) {
        const rgb = parseHex(p.seed[mode])!;
        let near = '', d = Infinity;
        for (const n of Object.keys(TONES[mode])) {
          const t = distance(rgb, tone(mode, n));
          if (t < d) { d = t; near = n; }
        }
        table.push(`${p.id}/${mode} nearest --${near} at ${d.toFixed(2)}`);
        worst = Math.min(worst, d);
      }
    expect(table, 'the shipped separations, for the record').toEqual([
      'blue/light nearest --info at 6.49',
      'blue/dark nearest --info at 18.71',
      'purple/light nearest --info at 20.79',
      'purple/dark nearest --info at 30.04',
      'green/light nearest --ok at 10.13',
      'green/dark nearest --ok at 14.89',
      'azure/light nearest --info at 20.25',
      'azure/dark nearest --info at 20.45',
    ]);
    // The closest shipped pair is still under the floor, and is still
    // stated as a number rather than described. What changed is WHICH
    // pair: it is blue against `--info` now, not green against `--ok`,
    // and only one of those is a tone a button can lie about.
    expect(worst, 'the closest shipped pair').toBeLessThan(TONE_FLOOR);
  });

  /** The seed green shipped on hue 142, retired 2026-09-06. It is the
   *  positive control below: the two tests that now assert an empty
   *  list would both pass on a `distance` that returned Infinity, and
   *  this hex is the one colour that must still be reported. */
  const RETIRED_GREEN = '#287d22';

  it('measured against the tones that MEAN something, no pack is close', () => {
    // The gate judges a picked colour against the stateful tones only —
    // success, warning, danger — because those are the ones a primary
    // button can lie about. Blue's 6.49 is entirely against `--info`,
    // and blue is 43.50 from the nearest tone that carries a state.
    //
    // This list was `['green/light']` until the hue moved. A pack that
    // fails the gate it imposes on the customer is the inconsistency
    // this file used to write down; now it holds the invariant instead.
    const below = (seeds: [string, string, AccentMode][]) =>
      seeds.filter(([, hex, mode]) =>
        Math.min(...STATEFUL_TONES.map((n) => distance(parseHex(hex)!, tone(mode, n))))
          < TONE_FLOOR).map(([id]) => id);

    const shipped: [string, string, AccentMode][] = [];
    for (const p of THEME_PACKS)
      for (const mode of MODES) shipped.push([`${p.id}/${mode}`, p.seed[mode], mode]);

    expect(shipped, 'no seeds measured — the assertion below is vacuous')
      .toHaveLength(THEME_PACKS.length * MODES.length);
    expect(below([...shipped, ['retired-green/light', RETIRED_GREEN, 'light']]),
      'the control colour stopped being reported — this test measures nothing')
      .toEqual(['retired-green/light']);
    expect(below(shipped), 'a shipped pack fails the gate it imposes on customers')
      .toEqual([]);
  });

  it('and it was the HUE that fixed it — no lightness on the band could', () => {
    // `--ok` is at hue 150. Green sat at 142, eight degrees away, and
    // reverting it to the band bought 5.71 → 6.09, which is all the
    // band had to give. 135 is seven degrees the other way and clears
    // the floor outright; the sweep below is what says no lightness
    // would have.
    const g = srgbToOklch(parseHex(THEME_PACKS.find((p) => p.id === 'green')!.seed.light)!);
    const okHue = TONES.light.ok[2];
    expect(Math.abs(g.H - okHue), 'green is back within eight degrees of --ok')
      .toBeGreaterThanOrEqual(12);

    // ON THE BAND, at the retired hue, at every lightness the band
    // admits. `fitAccent` does clear hue 142 — it returns a colour, and
    // the test below records that it moves `away from --ok` — but only
    // by walking OFF the band, which is a licence a customer's colour
    // has and a pack does not. A pack sits on the band by definition:
    // that is what makes four accents read as one family.
    const retiredHue = srgbToOklch(parseHex(RETIRED_GREEN)!).H;
    const L0 = ACCENT_BAND.light;
    let bestOnBand = 0;
    for (let L = L0 - BAND_TOLERANCE; L <= L0 + BAND_TOLERANCE + 1e-9; L += 0.001) {
      const rgb = oklchToSrgb(L, 0.15, retiredHue).rgb;
      bestOnBand = Math.max(bestOnBand,
        Math.min(...STATEFUL_TONES.map((n) => distance(rgb, tone('light', n)))));
    }
    expect(bestOnBand, 'a lightness ON THE BAND at hue 142 clears the floor after all')
      .toBeLessThan(TONE_FLOOR);
    // And the colour that DOES clear it is off the band — the proof
    // that the nudge is a different licence, not a lightness the pack
    // could have taken.
    const rescued = fitAccent(RETIRED_GREEN, 'light').rgb!;
    expect(Math.abs(srgbToOklch(rescued).L - L0),
      'the nudge stayed on the band, so the pack could have done the same')
      .toBeGreaterThan(BAND_TOLERANCE);
  });
});

describe('a picked colour is fitted or refused, never quietly wrong', () => {
  /** Every hue, at a chroma high enough to be a real accent. */
  const wheel = (mode: AccentMode) =>
    Array.from({ length: 360 }, (_, H) => toHex(oklchToSrgb(ACCENT_BAND[mode], 0.2, H).rgb));

  it('nothing it accepts sits closer than the floor to a tone that MEANS something', () => {
    // `--info` is out of scope by design — see STATEFUL_TONES. A button
    // the colour of the info tone lies about nothing, and holding every
    // blue to this floor would have been a colour policy wearing a
    // safety argument.
    const bad: string[] = [];
    for (const mode of MODES)
      for (const hex of wheel(mode)) {
        const { rgb } = fitAccent(hex, mode);
        if (!rgb) continue;
        for (const n of STATEFUL_TONES)
          if (distance(rgb, tone(mode, n)) < TONE_FLOOR)
            bad.push(`${mode} ${hex} -> ${toHex(rgb)} is ${distance(rgb, tone(mode, n)).toFixed(2)} from --${n}`);
      }
    expect(bad).toEqual([]);
  });

  it('and it does NOT refuse a colour merely for looking like --info', () => {
    // The inverse claim, which is the one the scope change is for: the
    // info hue is the hue most brands pick, and a gate that refused it
    // would refuse half the plausible accents.
    const infoish = toHex(oklchToSrgb(ACCENT_BAND.light, 0.15, TONES.light.info[2]).rgb);
    expect(fitAccent(infoish, 'light').rgb, 'an info-coloured accent was refused').not.toBeNull();
  });

  it('says so whenever it moved a colour, and never claims a move it did not make', () => {
    let moved = 0, still = 0;
    for (const mode of MODES)
      for (const hex of wheel(mode)) {
        const r = fitAccent(hex, mode);
        if (!r.rgb) continue;
        // Compared by LIGHTNESS, not by hex. Every colour here has been
        // through oklch and back, and that round trip moves a channel by
        // a step or two — comparing hex would call rounding a move.
        const L = srgbToOklch(r.rgb).L;
        if (r.movedFrom) {
          moved++;
          expect(Math.abs(L - ACCENT_BAND[mode]), `${mode} ${hex} reports a move it did not make`)
            .toBeGreaterThan(0.005);
        } else {
          still++;
          expect(Math.abs(L - ACCENT_BAND[mode]), `${mode} ${hex} moved without saying so`)
            .toBeLessThanOrEqual(0.005);
        }
      }
    // Both branches have to be exercised or this test is watching one.
    expect(moved, 'no colour was ever nudged — the tone check is not firing').toBeGreaterThan(0);
    expect(still, 'every colour was nudged — the band is wrong').toBeGreaterThan(0);
  });

  /**
   * Light mode only, and that is not a gap in the test.
   *
   * The dark tones live at L 0.76-0.82 while the accent band is 0.62, so
   * handing `--danger` to the picker in dark mode does not produce a
   * danger-coloured accent — it produces a much darker red that measures
   * 13.9 away from `--danger` itself. There is nothing to refuse. The
   * light tones sit at 0.49-0.52, inside the band, so they stay
   * themselves and the gate has to hold.
   */
  it('never hands back a light accent that reads as a tone that means something', () => {
    for (const name of STATEFUL_TONES) {
      const r = fitAccent(toHex(tone('light', name)), 'light');
      if (r.rgb === null) {
        expect(r.collidesWith, `--${name} refused without naming the tone`).toBe(name);
        continue;
      }
      // Accepted only by moving far enough away, and it must say so.
      expect(r.movedFrom, `--${name} was accepted unchanged`).toBe(name);
      expect(distance(r.rgb, tone('light', name)), `--${name} accepted too close`)
        .toBeGreaterThanOrEqual(TONE_FLOOR);
    }
  });

  it('a dark-mode tone is not a collision, because the band has already moved it', () => {
    for (const name of STATEFUL_TONES) {
      const r = fitAccent(toHex(tone('dark', name)), 'dark');
      expect(r.rgb, `dark --${name} refused`).not.toBeNull();
      expect(distance(r.rgb!, tone('dark', name))).toBeGreaterThanOrEqual(TONE_FLOOR);
    }
  });

  it('refuses what is not a colour at all', () => {
    for (const junk of ['', 'red', '#12', 'oklch(0.5 0.2 100)', '#gggggg'])
      expect(fitAccent(junk, 'dark').rgb, junk).toBeNull();
  });

  /**
   * The packs go to the page as CSS and never pass through here, so this
   * asks what WOULD happen if they did. Everything dark, and purple and
   * azure in light, sail through. Blue and green in light get nudged —
   * the same two the characterisation test records below the floor.
   */
  it('would nudge no shipped seed — every pack passes its own gate', () => {
    const nudged: string[] = [];
    for (const p of THEME_PACKS)
      for (const mode of MODES) {
        const r = fitAccent(p.seed[mode], mode);
        expect(r.rgb, `${p.id}/${mode} could not be rescued at all`).not.toBeNull();
        if (r.movedFrom) nudged.push(`${p.id}/${mode} away from --${r.movedFrom}`);
      }
    // Was `['green/light away from --ok']`. A pack reaches the page as
    // CSS and never passes through `fitAccent`, so this was the one
    // measurement saying our own green would have been corrected had a
    // customer typed it. The control below keeps the assertion honest.
    expect(fitAccent('#287d22', 'light').movedFrom,
      'the retired green stopped being nudged — fitAccent measures nothing')
      .toBe('ok');
    expect(nudged, 'a shipped seed needs the correction we apply to customers')
      .toEqual([]);
  });
});

describe('the tokens a custom accent installs', () => {
  const sample = ['#ff6a00', '#8b5cf6', '#0ea5e9', '#e11d48', '#14b8a6'];

  it('ships a label that reads on the colour it labels', () => {
    for (const mode of MODES)
      for (const hex of sample) {
        const r = accentTokens(hex, mode);
        if (!r.tokens) continue;
        const bg = parseHex(r.tokens['--primary'])!;
        const fg = parseHex(r.tokens['--primary-foreground'])!;
        expect(contrastRatio(fg, bg), `${mode} ${hex} label on primary`)
          .toBeGreaterThanOrEqual(AA_LARGE);
      }
  });

  it('ships a text variant that reads on the page canvas', () => {
    const canvas: Record<AccentMode, RGB> = { light: [1, 1, 1], dark: [0.039, 0.039, 0.039] };
    for (const mode of MODES)
      for (const hex of sample) {
        const r = accentTokens(hex, mode);
        if (!r.tokens) continue;
        expect(contrastRatio(parseHex(r.tokens['--primary-text'])!, canvas[mode]), `${mode} ${hex} --primary-text`)
          .toBeGreaterThanOrEqual(AA_LARGE);
      }
  });

  it('installs exactly the four it claims — never --chart-1', () => {
    const r = accentTokens('#ff6a00', 'dark');
    expect(Object.keys(r.tokens!).sort()).toEqual([
      '--primary', '--primary-foreground', '--primary-hover', '--primary-text',
    ]);
  });

  it('carries a refusal through instead of returning half a theme', () => {
    // Found by sweeping, not assumed: the hues that cannot be rescued at
    // all are a narrow band, and the test needs one that really is one.
    const refused = Array.from({ length: 360 }, (_, H) =>
      toHex(oklchToSrgb(ACCENT_BAND.light, 0.2, H).rgb))
      .map((hex) => ({ hex, r: accentTokens(hex, 'light') }))
      .find(({ r }) => r.tokens === null);
    expect(refused, 'no light hue is refused — the gate never closes').toBeDefined();
    expect(refused!.r.hex).toBeNull();
    expect(Object.keys(TONES.light)).toContain(refused!.r.collidesWith);
  });
});
